"""12. 데이터 프로파일링
데이터의 구조와 분포를 자동으로 조사하여 데이터의 특성을 파악합니다."""


# 프로파일링은 데이터를 바꾸기 전에 분포·형식·품질 특성을 조사하는 과정입니다.
# NumPy/Pandas는 수치 배열과 표, SciPy는 통계, IsolationForest는 이상치 후보, PIL은 이미지 속성을 다룹니다.
import hashlib
import json
import unicodedata
from itertools import combinations
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest
from PIL import Image
from .db import connect
from .config import DATA, REPORTS
from .generate import write_json
from .quality import normalize_seller


# 문자열을 길이 n의 겹치는 조각 집합으로 만듭니다. 빈도보다 어떤 조각이 포함되는지 비교하는 표현입니다.
def shingles(text, n=3):

    # NFC로 한글 조합 형태를 맞춘 뒤 일반 공백을 제거합니다.
    # 예: n=3, "가나다라" → {"가나다", "나다라"}. 길이가 n보다 짧으면 빈 집합입니다.
    text = unicodedata.normalize("NFC", text).replace(" ", "")
    return {text[i : i + n] for i in range(max(0, len(text) - n + 1))}


# 교집합 크기/합집합 크기로 집합 유사도를 계산합니다. 두 집합이 모두 비면 여기서는 동일한 것으로 정의합니다.
def jaccard(a, b):

    # &는 집합 교집합, |는 합집합입니다.
    # 공통 조각이 많을수록 1에 가까우며 둘 다 비면 여기서는 1로 정의합니다.
    return len(a & b) / len(a | b) if a | b else 1.0


# 모든 관측 쌍의 실제 판매자 ID와 예측 그룹을 비교합니다. precision은 잘못된 병합, recall은 놓친 병합을 점검합니다.
def pair_scores(observations, predicted):
    tp = fp = fn = 0

    # 모든 두 관측의 조합을 한 번씩 비교합니다.
    # actual은 실제 판매자 ID 일치, match는 정규화 결과 일치입니다.
    # tp=맞게 합침, fp=다른 판매자를 합침, fn=같은 판매자를 놓침입니다.
    for a, b in combinations(range(len(observations)), 2):
        actual = observations[a]["seller_id"] == observations[b]["seller_id"]
        match = predicted[a] == predicted[b]
        tp += actual and match
        fp += not actual and match
        fn += actual and not match
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "precision": tp / (tp + fp) if tp + fp else 0,
        "recall": tp / (tp + fn) if tp + fn else 0,
    }


# 수치 분포·문서 인코딩/중복·이미지 속성·판매자 표기·결측 기제를 조사하는 독립 실험을 보고서로 묶습니다.
def run():
    with connect() as conn:

        # 정형 프로파일에는 주문 금액/수량/단가/채널/상품을 읽습니다.
        # 상품 텍스트, raw 종류별 건수, 열린 격리 사유, 지연 결제는 별도 쿼리로 읽습니다.
        rows = conn.execute(
            "SELECT expected_krw,quantity,unit_price_krw,channel,product_id FROM orders"
        ).fetchall()
        products = conn.execute(
            "SELECT product_id,title,description,seller_id FROM products ORDER BY product_id"
        ).fetchall()
        raw = conn.execute(
            "SELECT source,COUNT(*) n FROM raw_events GROUP BY source"
        ).fetchall()
        issues = conn.execute(
            "SELECT reason,COUNT(*) n FROM quarantine WHERE status='open' GROUP BY reason"
        ).fetchall()
        late = conn.execute(
            "SELECT COUNT(*) n FROM payments WHERE received_at-occurred_at>interval '1 day'"
        ).fetchone()["n"]
        total_payments = conn.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]

    # DB의 딕셔너리 목록을 열 단위 분석이 쉬운 DataFrame으로 바꿉니다.
    # expected_krw는 quantity×unit_price_krw인 주문 기대 금액입니다.
    frame = pd.DataFrame(rows)

    # 통계 함수에 넘기기 위해 float 배열로 변환합니다.
    # 이 배열은 분석용이며 정수 원화 원장 값을 DB에서 바꾸지는 않습니다.
    amounts = frame.expected_krw.to_numpy(dtype=float)
    counts = frame.groupby("product_id").size().sort_values(ascending=False)
    # IQR, Z-score, 로그 변환 Z-score는 서로 다른 기준입니다. 탐지 건수가 다르다는 것만으로 어떤 방식이 정답인지는 알 수 없습니다.

    # 25/75백분위수 사이 폭이 IQR입니다.
    # 통상적인 1.5×IQR 경계 밖 값과 평균에서 3표준편차 넘는 Z-score를 각각 비교합니다.
    # log1p는 log(1+x)로 큰 금액의 치우침을 줄인 뒤 Z-score를 다시 구하는 실험입니다.
    q1, q3 = np.percentile(amounts, [25, 75])
    iqr = q3 - q1
    z = stats.zscore(amounts)
    logz = stats.zscore(np.log1p(amounts))
    # contamination=0.05는 모델의 이상치 판정 비율 설정이며 실제 오류율을 측정한 값이 아닙니다.
    model = IsolationForest(random_state=42, contamination=0.05)

    # 수량과 단가 두 열로 이상치 후보를 찾습니다.
    # IsolationForest 결과 -1은 이상 후보, 1은 일반 관측입니다.
    # 실제 오류 정답과 대조한 분류 성능이 아니라 설정에 따른 탐색 결과입니다.
    labels = model.fit_predict(frame[["quantity", "unit_price_krw"]])

    # 최대 5,000개 금액으로 정규성 검정을 합니다.
    # w는 검정 통계량, p는 정규분포 가정 아래 결과의 극단성을 나타내는 값입니다.
    # p가 작다고 해당 거래가 잘못되었다는 뜻은 아닙니다.
    w, p = stats.shapiro(amounts[:5000])

    # 평균/중앙값/p95는 중심과 꼬리 크기, skew는 비대칭 정도를 보여 줍니다.
    # 상위 10% 상품 주문 비중은 상품별 주문 횟수를 내림차순 정렬해 계산합니다.
    # 서로 다른 이상치 기준의 탐지 건수를 함께 저장해 차이를 관찰합니다.
    numeric = {
        "rows": len(frame),
        "mean": float(amounts.mean()),
        "median": float(np.median(amounts)),
        "p95": float(np.percentile(amounts, 95)),
        "skew": float(stats.skew(amounts)),
        "top_10pct_products_order_share": float(
            counts.head(max(1, int(np.ceil(len(counts) * 0.1)))).sum() / counts.sum()
        ),
        "shapiro": {
            "w": float(w),
            "p_value": float(p),
            "sample_size": min(5000, len(amounts)),
        },
        "outliers": {
            "iqr": int(((amounts < q1 - 1.5 * iqr) | (amounts > q3 + 1.5 * iqr)).sum()),
            "z_score": int((np.abs(z) > 3).sum()),
            "log_z_score": int((np.abs(logz) > 3).sum()),
            "isolation_forest": int((labels == -1).sum()),
        },
        "interpretation": "생성기가 만든 유한 분포. 정규성 기각만으로 데이터 오류라 판단하지 않음. IF 5%는 설정값이며 실제 사기율이 아님.",
    }
    corpus = DATA / "profile/documents"
    corpus.mkdir(parents=True, exist_ok=True)
    text = "휴대용 보온 텀블러: 따뜻한 음료를 담는 가벼운 물병입니다."

    # 내용이 같은 UTF-8 복사본, CP949 인코딩본, 일부 단어만 바꾼 문서, 전혀 다른 문서를 직접 만듭니다.
    # 내용 동일성과 bytes 동일성이 서로 다를 수 있음을 비교하기 위한 통제된 입력입니다.
    fixtures = {
        "original.txt": text.encode("utf-8"),
        "duplicate.txt": text.encode("utf-8"),
        "cp949.txt": text.encode("cp949"),
        "near.txt": text.replace("따뜻한", "차가운").encode("utf-8"),
        "different.txt": "장거리 달리기를 위한 가벼운 운동화입니다.".encode("utf-8"),
    }
    docs = []

    # name은 파일명, payload는 이미 인코딩된 bytes입니다.
    # write_bytes로 저장하고 UTF-8 해석이 실패하면 이 실험에 알려진 CP949로 다시 해석합니다.
    for name, payload in fixtures.items():
        file = corpus / name
        file.write_bytes(payload)
        # 이 실험은 입력을 UTF-8/CP949로 직접 만들었기에 이 순서로 복구합니다. 모든 인코딩을 자동 판별하는 알고리즘은 아닙니다.
        try:
            decoded = payload.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            decoded = payload.decode("cp949")
            encoding = "cp949"

        # bytes는 실제 파일 크기, chars는 디코딩 후 문자 수입니다.
        # sha256은 bytes 기준이고 text는 근사 문서 유사도 기준으로 사용됩니다.
        docs.append(
            {
                "file": name,
                "bytes": len(payload),
                "encoding": encoding,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "chars": len(decoded),
                "text": decoded,
                "line_count": len(decoded.splitlines()),
            }
        )
    similar = []
    # 바이트가 같으면 완전 중복이고, 디코딩한 문자 조각이 비슷하면 근사 중복 후보입니다. 인코딩 차이도 구별합니다.
    for a, b in combinations(docs, 2):
        score = jaccard(shingles(a["text"]), shingles(b["text"]))

        # Jaccard 0.5 이상을 근사 중복 후보로 기록하는 실습용 기준입니다.
        # 후보라고 파일을 삭제하거나 하나로 합치지 않고 쌍과 점수만 보고합니다.
        if score >= 0.5:
            similar.append(
                {
                    "a": a["file"],
                    "b": b["file"],
                    "jaccard": score,
                    "byte_equal": a["sha256"] == b["sha256"],
                }
            )
    images = []
    directory = DATA / "profile/images"
    directory.mkdir(parents=True, exist_ok=True)

    # RGB=색상 3채널, RGBA=투명도 포함 4채널, L=회색조입니다.
    # 해상도와 색 모드가 달라질 때 이미지 파일 속성이 어떻게 다른지 살펴봅니다.
    for mode, size in [("RGB", (160, 160)), ("RGBA", (80, 80)), ("L", (320, 240))]:
        path = directory / f"{mode}.png"
        Image.new(
            mode,
            size,
            (
                128
                if mode == "L"
                else ((30, 140, 140, 180) if mode == "RGBA" else (30, 140, 140))
            ),
        ).save(path)

        # 이미지를 열고 width/height/mode와 실제 파일 크기를 기록합니다.
        # with를 벗어나면 열린 파일 자원을 닫습니다. 픽셀 수와 압축된 파일 크기는 같은 개념이 아닙니다.
        with Image.open(path) as im:
            images.append(
                {
                    "file": path.name,
                    "mode": im.mode,
                    "width": im.width,
                    "height": im.height,
                    "bytes": path.stat().st_size,
                }
            )
    observations = []

    # 판매자 8명마다 세 가지 법인/공백 표기를 만듭니다.
    # 같은 seller_id의 관측들은 합쳐지고 다른 번호는 분리되는지 pair_scores로 평가합니다.
    for i in range(8):
        for label in [f"㈜ 상점 {i:02}", f"상점{i:02}(주)", f"주식회사 상점 {i:02}"]:
            observations.append({"seller_id": f"S{i:02}", "observed_name": label})

    # 각 이름에 normalize_seller를 적용해 예상 그룹 키를 만듭니다.
    # set(resolved)의 개수는 서로 다른 정규화 그룹 수입니다.
    resolved = [normalize_seller(o["observed_name"]) for o in observations]
    entity = pair_scores(observations, resolved)
    entity["observations"] = observations
    entity["canonical_groups"] = len(set(resolved))
    entity["limitation"] = (
        "고의로 쉬운 표기 변형 24건. 같은 상호의 다른 사업자·동명이인에는 이름만으로 자동 병합하지 않음."
    )

    # 결측 실험도 seed를 고정한 난수를 사용합니다.
    # MCAR는 무작위 누락, MAR는 관측 가능한 old 그룹에 따라 누락 확률이 달라짐,
    # MNAR는 누락되는 값 자체가 큰 경우 빠지는 상황을 합성합니다.
    rng = np.random.default_rng(42)
    n = 500
    old = rng.random(n) < 0.5
    underlying = rng.lognormal(10, 1, n)
    missing = {
        "MCAR": rng.random(n) < 0.1,
        "MAR": old & (rng.random(n) < 0.25),
        "MNAR": underlying > np.percentile(underlying, 90),
    }
    # 결측 원인을 알고 만든 합성 실험입니다. 결측 집단과 관측 집단의 실제 평균 차이로 선택 편향을 살펴봅니다.

    # 불리언 마스크 v가 True인 위치가 결측입니다.
    # v.mean()은 True를 1로 세므로 결측률이고, underlying[v]는 숨겨진 실제 값입니다.
    # 현실에서는 그 실제 값을 모르지만 여기서는 직접 생성했기에 편향을 비교할 수 있습니다.
    mechanisms = {
        k: {
            "missing_rate": float(v.mean()),
            "old_group_rate": float(v[old].mean()),
            "new_group_rate": float(v[~old].mean()),
            "true_mean_missing": float(underlying[v].mean()),
            "true_mean_observed": float(underlying[~v].mean()),
        }
        for k, v in missing.items()
    }
    mechanisms["interpretation"] = (
        "합성 주입 규칙을 알고 있어 기제를 구분할 수 있음. 실제 관측 데이터의 결측률 차이만으로 MCAR/MAR/MNAR을 증명할 수 없음."
    )

    # 문서 원문 text는 보고서에서 빼고 요약 속성만 남깁니다.
    # 품질의 완전성/유일성/유효성/정확성/일관성/적시성을 서로 다른 근거로 설명합니다.
    report = {
        "structured": numeric,
        "documents": [{k: v for k, v in d.items() if k != "text"} for d in docs],
        "near_duplicates": similar,
        "images": images,
        "unicode": {
            "nfc_nfd_same_bytes": "가".encode()
            == unicodedata.normalize("NFD", "가").encode(),
            "normalized_equal": unicodedata.normalize(
                "NFC", unicodedata.normalize("NFD", "가")
            )
            == "가",
        },
        "entity_resolution": entity,
        "missingness_lab": mechanisms,
        "quality_dimensions": {
            "completeness": "필수값 누락은 validate에서 격리. 선택 메타데이터 결측은 허용",
            "uniqueness": "이벤트 기본키 + payload hash; 반복 수신과 ID 충돌 구분",
            "validity": issues,
            "accuracy": "생성기 정답 합계와 원장 비교: demo_evidence.json",
            "consistency": "FK + 환불<=결제 + 마트/원장 합계 비교",
            "timeliness": {
                "over_24h_events": late,
                "total_payment_events": total_payments,
                "rate": late / total_payments,
            },
        },
        "raw_distinct_payloads": raw,
    }
    write_json(REPORTS / "profile.json", report)
    return {
        "rows": len(frame),
        "entity_resolution": {
            k: entity[k] for k in ["precision", "recall", "canonical_groups"]
        },
        "structured": numeric,
    }
