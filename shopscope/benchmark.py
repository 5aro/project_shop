"""11. 성능 측정
데이터 처리 방식에 따른 실행시간과 성능 차이를 측정합니다."""


# 성능 실험은 결과의 정확성, 시간, 메모리, 실행 조건을 함께 기록합니다.
# subprocess는 엔진별 프로세스 분리, statistics는 중앙값 계산, Counter는 부분 집계 누적에 사용합니다.
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from .config import DATA, REPORTS
from .generate import benchmark_csv, write_json


# 엔진 하나에서 유료 주문의 수량×단가를 채널별 합산합니다. 결과와 시간·프로세스 최대 메모리를 반환합니다.
def worker(engine, path):

    # 엔진 이름 접두어로 사용할 라이브러리를 고릅니다.
    # 라이브러리 import 뒤에 start를 잡으므로 elapsed_seconds는 import 시간을 제외합니다.
    if engine.startswith("pandas"):
        import pandas as pd

        start = time.perf_counter()
        if engine == "pandas_chunks":

            # Counter는 키별 숫자를 더하는 딕셔너리입니다.
            # 예: 첫 청크 app=100, 다음 청크 app=200이면 update 후 app=300이 됩니다.
            total = Counter()
            # 청크 단위로 읽고 부분 합계를 누적하면 전체 CSV를 한 번에 메모리에 올릴 필요가 없습니다.
            for df in pd.read_csv(
                path,

                # 한 번에 최대 20,000행만 읽습니다. channel/status는 반복 문자열이므로 category로,
                # 수량/단가는 범위를 고려한 작은 정수형으로 읽어 메모리를 줄입니다.
                # 일반 데이터에는 이 범위를 넘는 값이 있을 수 있으므로 무조건 같은 자료형을 적용하면 안 됩니다.
                chunksize=20000,
                dtype={
                    "channel": "category",
                    "status": "category",
                    "quantity": "int16",
                    "unit_price_krw": "int32",
                },
            ):

                # 불리언 마스크로 paid 행만 선택하고 독립적인 DataFrame으로 복사합니다.
                # 그 뒤 amount 열을 만들어 채널별 groupby.sum을 수행합니다.
                df = df[df.status == "paid"].copy()
                # 작은 정수형끼리 곱하면 범위를 넘을 수 있으므로 곱셈 전에 수량을 int64로 확장합니다.
                df["amount"] = df.quantity.astype("int64") * df.unit_price_krw

                # 각 청크의 채널별 합계를 Python 숫자로 바꿔 누적합니다.
                # observed=True는 범주형 자료에 실제 등장한 그룹만 결과에 포함합니다.
                total.update(
                    {
                        str(k): int(v)
                        for k, v in df.groupby("channel", observed=True)
                        .amount.sum()
                        .items()
                    }
                )
            result = dict(total)
        else:

            # 일괄 읽기는 전체 CSV를 메모리로 가져옵니다.
            # 같은 필터와 집계를 하되 청크 경계가 없으므로 코드가 단순한 대신 입력 크기에 따라 메모리 부담이 커집니다.
            df = pd.read_csv(path)
            df = df[df.status == "paid"].copy()
            df["amount"] = df.quantity * df.unit_price_krw
            result = {
                str(k): int(v) for k, v in df.groupby("channel").amount.sum().items()
            }
    elif engine.startswith("polars"):
        import polars as pl

        start = time.perf_counter()

        # eager는 즉시 읽고 처리하는 방식입니다.
        # scan_csv/scan_parquet는 아직 데이터를 모두 읽지 않고 실행 계획을 만드는 lazy 방식입니다.
        if engine == "polars_eager":
            frame = pl.read_csv(path)
        elif engine == "polars_parquet":
            frame = pl.scan_parquet(str(Path(path).with_suffix(".parquet")))
        else:
            frame = pl.scan_csv(path)

        # 표현식 pl.col은 열을 가리킵니다.
        # paid 필터 → channel 그룹 → quantity×unit_price의 합계라는 동일한 계산을 엔진별로 비교합니다.
        result_frame = (
            frame.filter(pl.col("status") == "paid")
            .group_by("channel")
            .agg((pl.col("quantity") * pl.col("unit_price_krw")).sum().alias("amount"))
        )
        # scan 계열은 아직 실행하지 않은 계획입니다. collect가 실제 계산을 시작하며 streaming 엔진을 요청합니다.
        if engine != "polars_eager":
            result_frame = result_frame.collect(engine="streaming")
        result = {r["channel"]: r["amount"] for r in result_frame.to_dicts()}
    elif engine == "dask":
        import dask.dataframe as dd

        start = time.perf_counter()

        # Dask는 CSV를 블록으로 나누고 계산 그래프를 구성합니다.
        # compute(...)를 호출할 때 실제 계산하며 여기서는 한 컴퓨터의 스레드 2개를 사용합니다.
        # 분산 클러스터 성능을 측정하는 실험은 아닙니다.
        df = dd.read_csv(path, blocksize="2MB")
        df = df[df.status == "paid"]
        df = df.assign(amount=df.quantity * df.unit_price_krw)
        result = {
            str(k): int(v)
            for k, v in df.groupby("channel")
            .amount.sum()
            .compute(scheduler="threads", num_workers=2)
            .items()
        }
    else:
        raise ValueError(engine)
    import resource

    # ru_maxrss 단위는 macOS에서 bytes, Linux에서 KiB이므로 MiB 환산식을 나눕니다. import 메모리도 포함됩니다.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    return {
        "engine": engine,
        "elapsed_seconds": time.perf_counter() - start,
        "result": result,
        "peak_rss_mib": peak,
    }


# 매번 별도 Python 프로세스로 실행해 엔진 사이의 메모리 상태 공유를 줄입니다. 시작/import를 포함하는 벽시계 시간도 따로 기록합니다.
def measured(engine, path):
    import psutil

    begin = time.perf_counter()

    # 현재 Python(sys.executable)으로 worker 모드를 실행합니다.
    # stdout/stderr를 PIPE로 받아 결과 JSON과 오류 메시지를 구분합니다.
    # 명령을 리스트로 전달하므로 셸 문자열 해석을 거치지 않습니다.
    proc = subprocess.Popen(
        [sys.executable, "-m", "shopscope.benchmark", "worker", engine, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:

        # 프로세스 출력을 함께 읽으며 최대 180초 기다립니다.
        # 시간을 넘기면 kill한 뒤 communicate를 다시 호출해 프로세스 자원을 정리합니다.
        stdout, stderr = proc.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("benchmark worker timed out")

    # 종료 코드 0은 정상, 그 외는 실패입니다.
    # 실패 출력은 예외로 전달하고 정상 출력만 JSON으로 해석합니다.
    if proc.returncode:
        raise RuntimeError(stderr)
    result = json.loads(stdout)
    result["process_wall_seconds"] = time.perf_counter() - begin
    return result


# 로그를 한 줄씩 읽어 시간별 오류 건수와 지연 시간 빈도표를 만듭니다. 원본 지연값 전체를 리스트로 보관하지 않습니다.
def parse_log(path):
    import re


    # 로그 형식은 "시각 INFO또는ERROR order=ID latency_ms=숫자"입니다.
    # ^와 $는 줄 전체 일치를, 괄호는 나중에 groups()로 꺼낼 부분을 나타냅니다.
    # 패턴을 반복문 전에 컴파일해 모든 줄에 재사용합니다.
    pattern = re.compile(r"^(\S+) (INFO|ERROR) order=(\S+) latency_ms=(\d+)$")
    hours = {}
    histogram = Counter()
    malformed = 0
    with open(path) as file:
        for line in file:

            # 형식이 다른 줄은 malformed를 늘리고 continue로 다음 줄로 넘어갑니다.
            # 지연 시간이 60,000ms보다 큰 값도 이 실험에서는 유효 범위 밖으로 집계합니다.
            match = pattern.match(line.strip())
            if not match:
                malformed += 1
                continue

            # 정규표현식에서 잡은 네 값을 언패킹합니다.
            # 주문 ID는 여기서 쓰지 않아 관례적으로 _에 받고 latency는 문자열에서 int로 바꿉니다.
            stamp, level, _, latency = match.groups()
            latency = int(latency)
            if latency > 60000:
                malformed += 1
                continue

            # ISO 시각의 앞 13문자는 YYYY-MM-DDTHH입니다.
            # 같은 날짜/시간대의 로그를 묶기 위한 키이며, 여기서는 같은 형식/시간대의 합성 로그를 전제합니다.
            hour = stamp[:13]

            # 처음 보는 시간 키면 [전체건수, 오류건수]=[0,0]을 넣고 그 목록을 돌려줍니다.
            # values[1] += level == "ERROR"는 True를 1, False를 0으로 더하는 표현입니다.
            values = hours.setdefault(hour, [0, 0])
            values[0] += 1
            values[1] += level == "ERROR"

            # 지연 시간 값마다 나타난 횟수를 기록합니다.
            # 예: 50ms가 세 번 나오면 {50: 3}이며 나중에 백분위수를 복원할 수 있습니다.
            histogram[latency] += 1
    return hours, dict(histogram), malformed


# 동일한 파서를 순차·스레드·프로세스로 실행한 뒤 부분 집계를 더합니다. 병렬화가 작은 입력에서 더 빠르다는 보장은 없습니다.
def analyze_logs(paths, mode="sequential", workers=2):
    start = time.perf_counter()
    if mode == "sequential":
        results = [parse_log(p) for p in paths]
    else:

        # mode="process"이면 별도 프로세스, 그 외 비순차 모드는 스레드 풀입니다.
        # 각 작업이 파일 하나를 파싱한 결과를 반환하고 메인 프로세스에서 합칩니다.
        pool = ProcessPoolExecutor if mode == "process" else ThreadPoolExecutor
        with pool(max_workers=workers) as executor:
            results = list(executor.map(parse_log, paths))
    hourly = {}
    histogram = Counter()
    malformed = 0

    # 병렬 작업이 공유 딕셔너리를 직접 수정하지 않도록 부분 결과를 모아 합산합니다.
    # 시간별 건수는 두 카운터를 각각 더하고 지연 시간 빈도는 Counter.update로 합칩니다.
    for hours, hist, bad in results:
        malformed += bad
        histogram.update(hist)
        for hour, (total, errors) in hours.items():
            counts = hourly.setdefault(hour, [0, 0])
            counts[0] += total
            counts[1] += errors
    return {
        "mode": mode,
        "seconds": time.perf_counter() - start,
        "hourly": hourly,
        "histogram": dict(histogram),
        "malformed": malformed,
    }


# 빈도표를 정렬된 표본처럼 보고 백분위 위치 양옆 값을 선형 보간합니다. 빈 표본은 None을 반환합니다.
def histogram_percentile(histogram, p):
    import math


    # 관측값 개수는 키 개수가 아니라 빈도들의 합입니다.
    # 예: {1: 2, 5: 1, 10: 1}은 서로 다른 값 3개지만 표본은 총 4개입니다.
    n = sum(histogram.values())
    if n == 0:
        return None
    # 예: 표본 [1,1,5,10]의 중앙값 위치는 1.5이므로 두 가운데 값 1과 5를 보간해 3을 얻습니다.
    position = (n - 1) * p / 100
    lo = math.floor(position)
    hi = math.ceil(position)
    found = {}
    count = 0

    # 값을 작은 순서대로 읽으며 각 값이 차지하는 표본 위치 구간을 찾습니다.
    # lo/hi가 그 구간에 들어오면 해당 값을 저장해 두 위치 사이를 선형 보간합니다.
    for value, frequency in sorted(histogram.items()):
        if count <= lo < count + frequency:
            found["lo"] = value
        if count <= hi < count + frequency:
            found["hi"] = value
        count += frequency
    return found["lo"] + (found["hi"] - found["lo"]) * (position - lo)


# 입력 생성 → 여러 엔진 반복 측정 → 결과 일치 확인 → 메모리/NumPy/로그 실험 → 보고서 저장 순서입니다.
def run(rows=200000, repeats=3):
    import numpy as np
    import pandas as pd
    import polars as pl
    import importlib.metadata

    if rows < 1 or repeats < 1:
        raise ValueError("rows/repeats must be positive")
    path = benchmark_csv(rows)

    # 같은 데이터를 Parquet로 미리 변환합니다.
    # 이 변환 비용은 worker의 집계 시간에 포함되지 않으므로 일회성 CSV 작업의 전체 비용과 구분합니다.
    pl.read_csv(path).write_parquet(path.with_suffix(".parquet"))
    query = (
        pl.scan_parquet(path.with_suffix(".parquet"))
        .filter(pl.col("status") == "paid")
        .group_by("channel")
        .agg((pl.col("quantity") * pl.col("unit_price_krw")).sum())
    )

    # explain은 lazy 실행 계획을 문자열로 보여 줍니다.
    # 필터나 열 선택이 읽기 단계 쪽으로 이동했는지 확인하는 학습 자료입니다.
    (REPORTS / "polars_plan.txt").write_text(query.explain())
    engines = [
        "pandas_eager",
        "pandas_chunks",
        "polars_eager",
        "polars_lazy",
        "polars_parquet",
        "dask",
    ]
    trials = []
    rng = random.Random(42)

    # 반복마다 엔진 순서를 섞고 개별 측정값을 모두 저장합니다.
    # 나중에 중앙값을 내더라도 원시 측정치가 남아 이상하게 튄 실행을 확인할 수 있습니다.
    for repeat in range(repeats):
        order = engines.copy()
        # 실행 순서를 섞어 특정 엔진이 항상 먼저 실행되는 영향을 줄입니다. OS 파일 캐시를 비우는 실험은 아닙니다.
        rng.shuffle(order)
        for engine in order:
            result = measured(engine, path)
            result["trial"] = repeat + 1
            trials.append(result)
    # 속도 비교 전에 집계 결과부터 확인합니다. 계산이 다른 엔진을 더 빠르다고 평가할 수는 없습니다.
    baseline = trials[0]["result"]
    assert all(t["result"] == baseline for t in trials), "엔진별 계산 결과 불일치"

    # 엔진별 시간은 중앙값, 메모리는 관측 최대값을 요약합니다.
    # 중앙값은 단일 느린 실행의 영향을 줄이지만 반복 3회만으로 정밀한 통계 결론을 내릴 수는 없습니다.
    summary = []
    for engine in engines:
        group = [t for t in trials if t["engine"] == engine]
        summary.append(
            {
                "engine": engine,
                "median_seconds": statistics.median(
                    t["elapsed_seconds"] for t in group
                ),
                "peak_rss_mib": max(t["peak_rss_mib"] for t in group),
            }
        )

    # 동일 DataFrame을 복사해 자료형만 바꾸고 deep=True로 문자열 메모리까지 비교합니다.
    # 범주형은 반복 값의 사전과 코드로 표현하여 반복 문자열 저장 비용을 줄일 수 있습니다.
    original = pd.read_csv(path, dtype={"channel": "object", "status": "object"})
    optimized = original.copy()
    optimized["channel"] = optimized.channel.astype("category")
    optimized["status"] = optimized.status.astype("category")
    optimized["quantity"] = pd.to_numeric(optimized.quantity, downcast="integer")
    optimized["unit_price_krw"] = pd.to_numeric(
        optimized.unit_price_krw, downcast="integer"
    )
    memory = {
        "before_bytes": int(original.memory_usage(deep=True).sum()),
        "after_bytes": int(optimized.memory_usage(deep=True).sum()),
    }

    # 동일 계산 2x+1을 Python 반복과 NumPy 배열 연산으로 수행합니다.
    # int64 배열로 자료형을 고정하고 array_equal로 두 결과가 같은지 먼저 확인합니다.
    values = np.arange(rows, dtype=np.int64)
    start = time.perf_counter()
    loop = [int(x) * 2 + 1 for x in values]
    loop_time = time.perf_counter() - start
    start = time.perf_counter()
    vector = values * 2 + 1
    vector_time = time.perf_counter() - start
    assert np.array_equal(loop, vector)
    # 슬라이스 view는 원본 메모리를 공유하고 copy는 분리됩니다. 아래 view 변경은 values에도 반영됩니다.
    view = values[:3]
    copy = values[:3].copy()

    # view가 원본 메모리를 공유하므로 원본 values의 처음 세 값도 바뀝니다.
    # copy는 별도 메모리이므로 이 변경을 따라가지 않습니다.
    view += 1
    numpy_lab = {
        "loop_seconds": loop_time,
        "vector_seconds": vector_time,
        "equal": True,
        "view_shares_memory": bool(np.shares_memory(values, view)),
        "copy_shares_memory": bool(np.shares_memory(values, copy)),
        "broadcast_shape": list((np.ones((3, 1)) + np.arange(4)).shape),
    }

    # 같은 로그 파일들을 세 가지 실행 방식으로 처리합니다.
    # 시간뿐 아니라 hourly와 histogram의 내용까지 같은지 확인해 병렬화로 결과가 달라지지 않았는지 검사합니다.
    log_results = [
        analyze_logs(sorted((DATA / "logs").glob("*.log")), mode)
        for mode in ["sequential", "thread", "process"]
    ]
    assert all(
        r["hourly"] == log_results[0]["hourly"]
        and r["histogram"] == log_results[0]["histogram"]
        for r in log_results
    )
    hist = log_results[0]["histogram"]

    # 시간대별 오류건수/전체건수로 오류율 배열을 만듭니다.
    # 평균보다 2표준편차 초과인 시간을 spikes로 표시합니다. 이는 탐색 기준이며 장애 원인을 입증하지는 않습니다.
    rates = np.array([v[1] / v[0] for v in log_results[0]["hourly"].values()])
    sd = float(rates.std()) if len(rates) else 0
    spikes = [
        {"hour": h, "rate": v[1] / v[0]}
        for h, v in log_results[0]["hourly"].items()
        if sd and (v[1] / v[0] - rates.mean()) / sd > 2
    ]

    # 환경·버전·개별 측정·요약·해석 한계를 함께 저장합니다.
    # 벤치마크 수치는 입력 크기와 컴퓨터, 캐시 상태가 달라지면 달라질 수 있기 때문입니다.
    report = {
        "rows": rows,
        "repeats": repeats,
        "machine": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "versions": {
            p: importlib.metadata.version(p)
            for p in ["numpy", "pandas", "polars", "dask", "pyarrow"]
        },
        "summary": summary,
        "trials": trials,
        "all_results_equal": True,
        "memory": memory,
        "numpy": numpy_lab,
        "logs": {
            "runs": [
                {k: r[k] for k in ["mode", "seconds", "malformed"]} for r in log_results
            ],
            "all_results_equal": True,
            "latency_percentiles_ms": {
                str(p): histogram_percentile(hist, p) for p in [50, 95, 99]
            },
            "spikes": spikes,
        },
        "limitations": [
            "로컬 합성 데이터; OS 파일 캐시를 비우지 않은 반복 실행",
            "RSS는 resource.getrusage의 단일 프로세스 high-water mark이며 import 메모리 포함",
            "각 엔진 별도 프로세스; elapsed는 import 제외, process_wall은 import 포함",
            "Dask는 단일 Mac의 스레드 스케줄러이며 다중 노드 실험이 아님",
            "로그 실험은 한 번 실행한 비교이며 속도 향상을 보장하지 않음",
        ],
    }
    write_json(REPORTS / "benchmark.json", report)
    lines = [
        "# 처리 엔진 비교",
        "",
        f"합성 {rows:,}행, {repeats}회. 집계 결과 일치: True.",
        "",
        "| 엔진 | 실행 중앙값(초) | 관측 피크 RSS(MiB) |",
        "|---|---:|---:|",
    ]
    for r in summary:
        lines.append(
            f"| {r['engine']} | {r['median_seconds']:.4f} | {r['peak_rss_mib']:.1f} |"
        )
    lines += [
        "",
        "메모리 최적화 전후: " + str(memory),
        "",
        "## 해석",
        "작은 데이터에서는 Dask 작업 그래프와 프로세스 시작 비용이 이득보다 클 수 있습니다. 빠르다는 결론보다 결과 일치와 측정 조건을 먼저 봅니다.",
        "Parquet 변환 비용은 집계 시간에서 제외했습니다. 일회성 CSV 처리와 반복 분석의 총비용은 다릅니다.",
        "정확한 환경·개별 측정치·순차/스레드/프로세스 결과는 benchmark.json에 있습니다.",
    ]
    (REPORTS / "benchmark.md").write_text("\n".join(lines))
    return {"rows": rows, "summary": summary, "all_results_equal": True}



# 다른 모듈에서 import할 때는 실행되지 않고 -m shopscope.benchmark로 실행할 때만 진입합니다.
# measured가 worker, 엔진 이름, 파일 경로를 argv[1:4]로 전달합니다.
if __name__ == "__main__":
    print(json.dumps(worker(sys.argv[2], sys.argv[3])))
