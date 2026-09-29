"""
2. 테스트 원천 데이터 생성
실제 외부 서비스 대신 데이터 파이프라인을 실습할 수 있도록 가상의 커머스 데이터를 생성합니다.
"""

import csv
import hashlib
import json
import random
from decimal import Decimal
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from .config import DATA


# timezone은 고정 UTC 오프셋을 가진 시간대 객체입니다.
# 한국은 UTC+9이므로 timedelta(hours=9)를 전달합니다. 이후 생성하는 시각에 이 시간대를 붙입니다.
KST = timezone(timedelta(hours=9))
# 상품 카테고리
CATEGORIES = ["생활", "전자", "패션", "스포츠"]
# 상품 이름
NAMES = [
    ("보온 텀블러", "온도를 유지하는 휴대용 물병 insulated travel bottle"),
    ("무선 헤드폰", "음악 감상 소음 차단 wireless music headphones"),
    ("데일리 백팩", "가벼운 출퇴근 가방 lightweight commuter backpack"),
    ("러닝 운동화", "달리기 쿠션 신발 running cushion shoes"),
]


# Python 객체를 일관된 JSON 문자열로 변환
# 키 정렬과 공백 규칙을 고정하여 딕셔너리 키 순서가 달라도 같은 JSON 표현과 해시를 얻습니다.
def encode(obj):

    # ensure_ascii=False는 한글을 \uXXXX 형태로 바꾸지 않고 보존합니다.
    # sort_keys=True는 키 순서를 고정하고 separators는 불필요한 공백을 없앱니다.
    # 결과는 파일이 아니라 문자열이며, 저장이나 해시는 호출자가 수행합니다.
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# 데이터의 해시값 생성
# 정규화된 JSON의 SHA-256을 계산합니다. 이벤트 ID와 함께 사용하면 같은 ID의 내용 변경을 구별할 수 있습니다.
def digest(obj):

    # encode(obj)는 JSON 문자열, 그 뒤 .encode()는 UTF-8 bytes 변환입니다.
    # sha256(...).hexdigest()는 내용을 64자리 16진수 문자열로 요약합니다. 암호화/복호화 용도는 아닙니다.
    return hashlib.sha256(encode(obj).encode()).hexdigest()


# JSON 파일을 저장
# 부모 폴더를 만든 뒤 임시 파일을 교체합니다. 단일 파일 저장 방식이며 여러 파일을 하나의 트랜잭션으로 묶지는 않습니다.
def write_json(path, value):

    # 문자열이나 Path로 받은 입력을 Path로 통일합니다.
    # path.parent는 파일이 들어갈 폴더이며, write_text 전에 이 폴더부터 만듭니다.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 임시 파일로 저장이 완료된 이후 원본파일과 교체
    tmp = path.with_suffix(path.suffix + ".tmp")

    # indent=2는 사람이 읽기 좋게 들여쓰기를 넣습니다.
    # default=json_value는 기본 JSON 변환기가 모르는 타입을 만났을 때 호출할 함수입니다.
    # UTF-8로 저장을 완료한 뒤 replace로 최종 파일 이름에 반영합니다.
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_value),
        encoding="utf-8",
    )
    tmp.replace(path)


# 기본 json.dump()가 처리하지 못하는 Python 객체 변환
# JSON에 없는 Decimal/date 타입의 직렬화 규칙입니다. 정수 원화는 int, 소수는 float, 날짜는 ISO 문자열로 바꿉니다.
def json_value(value):

    # DB의 정밀 숫자가 Decimal로 넘어올 수 있습니다.
    # 소수 부분이 없으면 int로 보존하고, 소수가 있으면 float로 바꿉니다.
    # float 변환에는 정밀도 한계가 있으므로 금액 원장은 정수 원화를 사용합니다.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


# 테스트 데이터 생성
# 고정 seed로 재현 가능한 합성 데이터를 만듭니다. 정상 거래 합계와 별도로 중복·오류·지연 이벤트를 주입합니다.
def generate(days=30, per_day=200, seed=42, root=DATA, start="2026-08-01"):
    if days < 1 or per_day < 1:
        raise ValueError("days와 per_day는 양수여야 합니다")
    root = Path(root)
    # 데이터 조건 명세서
    spec = dict(days=days, per_day=per_day, seed=seed, start=start, generator_version=1)

    # 이미 생성 완료 표시인 spec.json이 있으면 조건을 비교합니다.
    # 조건이 같을 때만 기존 truth를 반환하고 파일을 다시 만들지 않습니다.
    # 다른 조건의 실험은 기존 데이터를 덮어쓰기보다 별도 root를 사용하도록 합니다.
    if (root / "source/spec.json").exists():
        # 재현성 확보(reproducibility)
        if json.loads((root / "source/spec.json").read_text()) != spec:
            raise ValueError(
                "기존 데이터와 생성 조건이 다릅니다. 별도 --root를 사용하세요."
            )
        return json.loads((root / "source/truth.json").read_text())
    # 전역 난수 상태와 분리한 생성기입니다. 같은 seed와 생성 조건은 같은 난수 순서를 만듭니다.
    rng = random.Random(seed)
    products = []
    # 상품 80개 생성

    # i는 0부터 79까지입니다. i % 4로 네 이름/카테고리를 순환합니다.
    # f"P{i:04}"는 숫자를 4자리로 0 채우므로 P0000, P0001처럼 일정한 ID를 만듭니다.
    for i in range(80):
        name, desc = NAMES[i % 4]
        products.append(
            dict(
                product_id=f"P{i:04}",
                title=f"{name} {i:02}",
                category=CATEGORIES[i % 4],
                seller_id=f"S{i%8:02}",
                price_krw=(i % 19 + 5) * 1000,
                description=desc,
                metadata={
                    "color": ["black", "white", "blue"][i % 3],
                    "tags": [CATEGORIES[i % 4]],
                    "source": "synthetic",
                },
            )
        )
    write_json(root / "source/products.json", products)
    # 날짜별 데이터 저장

    # 아직 없는 날짜 키를 처음 접근하면 orders/payments/shipments의 빈 목록이 자동으로 생깁니다.
    # 늦은 배송·환불이 미래 날짜를 새로 만들기 때문에 편리합니다.
    # lambda는 호출 때마다 새 딕셔너리와 목록을 만드는 짧은 함수입니다.
    buckets = defaultdict(lambda: {"orders": [], "payments": [], "shipments": []})
    # 결과 검증을 위한 정답지

    # 이 정답지는 최종 DB를 다시 조회해서 만든 값이 아닙니다.
    # 정상 거래를 생성할 때 수량과 금액을 누적해 두므로 적재 결과와 비교할 기준이 됩니다.
    truth = dict(
        synthetic=True,
        seed=seed,
        orders=days * per_day,
        captured_krw=0,
        refunded_krw=0,
        refunds=0,
        duplicate_rows=0,
        injected_bad_orders=0,
        orphan_payments=1,
    )

    # 시작일을 date로 바꾸어 timedelta로 날짜를 더할 수 있게 합니다.
    # logs는 문자열 로그, clicks는 고객 행동 이벤트를 별도 수집하는 목록입니다.
    start_date = date.fromisoformat(start)
    logs = []
    clicks = []
    # 주문 생성
    for day in range(days):

        # 바깥 반복은 날짜, 안쪽 반복은 그날의 주문 번호입니다.
        # 이중 반복의 정상 주문 수는 days × per_day입니다.
        business_date = start_date + timedelta(days=day)
        bucket = buckets[str(business_date)]
        for i in range(per_day):

            # 상품을 난수로 선택하고 하루 중 임의 분에 주문 시각을 만듭니다.
            # datetime.combine은 날짜와 00:00 시각을 합치고, 그 위에 0~1439분을 더합니다.
            p = rng.choice(products)
            ts = datetime.combine(business_date, datetime.min.time(), KST) + timedelta(
                minutes=rng.randrange(1440)
            )
            oid = f"O{business_date:%Y%m%d}-{i:05}"
            qty = rng.randint(1, 4)
            customer = f"C{rng.randrange(max(10,days*per_day//4)):05}"

            # 주문 당시 수량·단가를 넣으므로 나중에 상품 가격이 바뀌어도 기대 금액을 계산할 수 있습니다.
            # metadata의 coupon은 i % 7 == 0인 주문에만 True가 되는 합성 속성입니다.
            order = dict(
                order_id=oid,
                customer_id=customer,
                product_id=p["product_id"],
                channel=rng.choice(["web", "app", "market"]),
                ordered_at=ts.isoformat(),
                quantity=qty,
                unit_price_krw=p["price_krw"],
                metadata={
                    "seller_label": f'㈜ 상점 {int(p["seller_id"][1:])}',
                    "coupon": i % 7 == 0,
                },
            )
            bucket["orders"].append(order)
            # 결제 생성
            amount = qty * p["price_krw"]
            # 분할 결제 생성

            # 13개마다 하나는 결제 금액을 두 부분으로 나눕니다.
            # amount//2는 정수 나눗셈이고 나머지 부분은 amount-첫부분이므로 합계가 정확히 amount입니다.
            parts = [amount // 2, amount - amount // 2] if i % 13 == 0 else [amount]

            # enumerate는 (순번, 값)을 함께 줍니다.
            # 분할 결제의 각 부분에 PAY0, PAY1처럼 서로 다른 이벤트 ID를 붙입니다.
            for j, part in enumerate(parts):
                payment = dict(
                    event_id=f"{oid}-PAY{j}",
                    order_id=oid,
                    kind="capture",
                    amount_krw=part,
                    occurred_at=ts.isoformat(),  # Event time
                    received_at=ts.isoformat(),  # Ingestion time
                    metadata={"provider": "demo-pay"},
                )
                bucket["payments"].append(payment)
                truth["captured_krw"] += part
                # 중복 결제 생성

                # 같은 결제 dict의 얕은 복사본을 추가하여 재전송 상황을 만듭니다.
                # 정답 결제액은 위에서 한 번만 더하고 duplicate_rows만 증가시킵니다.
                if i % 37 == 0:
                    bucket["payments"].append(payment.copy())
                    truth["duplicate_rows"] += 1
            # 환불 생성
            if i % 11 == 0:

                # 환불은 주문 이틀 후 발생하고 다시 이틀 후 수신되는 것으로 만듭니다.
                # occurred_at과 received_at을 나눠야 실제 발생 지연과 수집 지연을 구별할 수 있습니다.
                occurred = ts + timedelta(days=2)
                received = occurred + timedelta(days=2)
                refund = dict(
                    event_id=f"{oid}-REF1",
                    order_id=oid,
                    kind="refund",
                    amount_krw=p["price_krw"],
                    occurred_at=occurred.isoformat(),
                    received_at=received.isoformat(),
                    metadata={"reason": "change_of_mind"},
                )
                # 환불은 발생일이 아니라 수신일 파티션에 넣습니다. 주문 뒤 4일 늦게 들어오는 데이터를 재현합니다.
                buckets[str(received.date())]["payments"].append(refund)
                truth["refunded_krw"] += p["price_krw"]
                truth["refunds"] += 1
            # 배송 생성

            # 약속은 주문 3일 뒤, 실제 완료는 보통 2일 뒤이며 5개마다 하나는 4일 뒤입니다.
            # 따라서 지연된 배송을 원장에 정상 보관한 뒤 지표로 계산하는 연습을 할 수 있습니다.
            promised = ts + timedelta(days=3)
            delivered = ts + timedelta(days=4 if i % 5 == 0 else 2)
            buckets[str(delivered.date())]["shipments"].append(
                dict(
                    event_id=f"{oid}-DEL1",
                    order_id=oid,
                    carrier=["parcel-a", "parcel-b", "parcel-c"][i % 3],
                    promised_at=promised.isoformat(),
                    delivered_at=delivered.isoformat(),
                    received_at=delivered.isoformat(),
                )
            )
            hour = ts.hour

            # 삼항 표현식 A if 조건 else B입니다.
            # 03~04시에는 오류를 더 자주 넣어 시간대별 오류율 급증을 분석할 수 있게 합니다.
            error = i % 3 == 0 if hour in (3, 4) else i % 43 == 0
            # 로그 데이터 생성
            logs.append(
                f'{ts.isoformat()} {"ERROR" if error else "INFO"} order={oid} latency_ms={rng.randint(400,1800) if error else rng.randint(10,150)}'
            )
            # 고객 행동 데이터 생성

            # 같은 고객의 view/cart/buy를 0분, 5분, 40분 시점에 만듭니다.
            # 5분→40분은 35분 공백이지만, 같은 고객의 다른 주문 클릭이 끼면 실제 세션 결과는 달라질 수 있습니다.
            for j, minute in enumerate([0, 5, 40]):
                clicks.append(
                    dict(
                        event_id=f"{oid}-click{j}",
                        customer_id=customer,
                        occurred_at=(ts + timedelta(minutes=minute)).isoformat(),
                        action=["view", "cart", "buy"][j],
                    )
                )
        # 비정상 주문 생성

        # 그날 첫 주문을 복사한 뒤 ID와 수량만 바꿉니다.
        # quantity=-2는 품질 검사에서 격리되며 정답 정상 주문 수에는 포함하지 않습니다.
        bad = dict(bucket["orders"][0], order_id=f"BAD-{day}", quantity=-2)
        bucket["orders"].append(bad)
        truth["injected_bad_orders"] += 1
    # 주문이 존재하지 않는 결제(무결성 검증)
    ts = datetime.combine(start_date, datetime.min.time(), KST).isoformat()

    # 존재하지 않는 주문을 참조하는 결제 한 건을 넣습니다.
    # 필드 형식은 정상이어도 참조 무결성 검사에서 orphan_order가 되는 사례입니다.
    buckets[str(start_date)]["payments"].append(
        dict(
            event_id="ORPHAN-1",
            order_id="DOES-NOT-EXIST",
            kind="capture",
            amount_krw=1000,
            occurred_at=ts,
            received_at=ts,
            metadata={},
        )
    )

    # 날짜순으로 파티션을 저장합니다.
    # 여기에는 주문 생성 기간을 넘어선 배송/환불 수신 날짜도 포함됩니다.
    for day, sources in sorted(buckets.items()):
        for source, rows in sources.items():
            # 날짜 별로 저장
            path = root / "source" / day / f"{source}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(encode(r) + "\n" for r in rows), encoding="utf-8")
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # logs[i::4]는 i번째부터 4칸 간격으로 선택합니다.
    # 전체 로그를 네 파일에 분산시켜 순차·스레드·프로세스 읽기 성능을 비교할 입력으로 사용합니다.
    for i in range(4):
        (log_dir / f"checkout-{i}.log").write_text("\n".join(logs[i::4]) + "\n")
    write_json(root / "source/clicks.json", clicks)
    truth["net_collected_krw"] = truth["captured_krw"] - truth["refunded_krw"]
    # 배송·환불 때문에 주문 생성 기간 이후 날짜도 포함됩니다. 이 날짜들까지 적재해야 정답 합계가 맞습니다.
    truth["dates"] = sorted(buckets)
    write_json(root / "source/truth.json", truth)

    # spec는 모든 데이터와 truth를 쓴 뒤 마지막에 남깁니다.
    # 다음 generate 호출에서 이미 같은 조건으로 생성했는지 판단하는 완료 표시 역할입니다.
    write_json(root / "source/spec.json", spec)
    return truth


# 성능 비교용 데이터
# 처리 엔진들이 같은 입력을 읽도록 규칙적인 CSV를 만듭니다. 파일 작성 비용은 각 엔진 집계 측정에 포함하지 않습니다.
def benchmark_csv(rows=200000, root=DATA):
    path = Path(root) / "bench/orders.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    # csv.writer가 줄바꿈을 관리하므로 newline=""로 엽니다.
    # 첫 writerow는 열 이름, 반복문의 writerow는 실제 데이터 한 행입니다.
    with path.open("w", newline="") as f:
        writer = csv.writer(f)

        # channel/status는 반복되는 범주형 값, quantity/price는 정수입니다.
        # 이 구성을 통해 category 변환과 정수형 축소에 따른 메모리 변화를 비교할 수 있습니다.
        writer.writerow(["channel", "quantity", "unit_price_krw", "status"])
        for i in range(rows):
            writer.writerow(
                (
                    ["web", "app", "market"][i % 3],
                    i % 4 + 1,
                    (i % 19 + 5) * 1000,
                    "paid" if i % 7 else "cancelled",
                )
            )
    return path
