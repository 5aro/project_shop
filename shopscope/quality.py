"""5. 데이터 검증, 품질 규칙
수집된 데이터가 파이프라인에 적재하기 적합한지 검사합니다."""


# unicodedata는 눈에 같아 보이는 문자 표현을 통일합니다.
# re는 정규표현식, datetime은 날짜/시각 문자열 해석에 사용합니다.
import unicodedata
import re
from datetime import datetime


# ISO 시각 문자열을 datetime으로 바꾸고 시간대 누락을 거부합니다. 서로 다른 지역의 시각을 비교하려면 시간대가 필요합니다.
def timestamp(value):

    # ISO 시각으로 해석하기 전에 자료형을 검사합니다.
    # None이나 숫자는 이 함수가 받기로 약속한 입력이 아니므로 즉시 거부합니다.
    if not isinstance(value, str):
        raise ValueError("timestamp must be string")

    # 예: "2026-08-01T01:00:00+09:00" → 시간대가 있는 datetime 객체
    # +09:00은 UTC보다 9시간 빠른 시각임을 나타냅니다.
    result = datetime.fromisoformat(value)

    # 시간대가 없는 naive datetime과 시간대가 있는 aware datetime을 구별합니다.
    # 시각 비교에 지역 해석이 끼어들지 않도록 시간대 없는 입력은 허용하지 않습니다.
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone required")
    return result


# bool은 int의 하위 타입이므로 isinstance 대신 정확한 타입을 검사합니다. True가 수량 1로 통과하는 것을 막습니다.
def positive_integer(value, maximum=10**12):

    # 연쇄 비교 0 < value <= maximum은 두 조건을 모두 검사합니다.
    # 예: 2는 통과하지만 0, -1, 2.0, "2", True는 통과하지 않습니다.
    return type(value) is int and 0 < value <= maximum


# 정상은 None, 비정상은 첫 번째 오류 사유 문자열을 반환합니다. 행 형식 검증이며 DB 참조 관계는 pipeline.put_row에서 검사합니다.
def validate(source, row):

    # JSON 한 행이 객체인지 검사합니다.
    # 배열이나 문자열이면 row["order_id"] 같은 키 접근을 하기 전에 오류 사유를 반환합니다.
    if not isinstance(row, dict):
        return "invalid_object"
    # 원천별 필수 키를 정의합니다. metadata는 생략 가능하지만 제공했다면 객체여야 합니다.

    # 세 종류의 필수 키 목록 중 [source]로 현재 원천의 목록을 고릅니다.
    # source는 이 프로젝트의 orders/payments/shipments 중 하나라는 전제입니다.
    # 알 수 없는 source는 이 함수에서 오류 사유로 바꾸지 않고 KeyError가 납니다.
    required = {
        "orders": [
            "order_id",
            "customer_id",
            "product_id",
            "channel",
            "ordered_at",
            "quantity",
            "unit_price_krw",
        ],
        "payments": [
            "event_id",
            "order_id",
            "kind",
            "amount_krw",
            "occurred_at",
            "received_at",
        ],
        "shipments": [
            "event_id",
            "order_id",
            "carrier",
            "promised_at",
            "delivered_at",
            "received_at",
        ],
    }[source]

    # any는 하나라도 참이면 True입니다.
    # 키가 없거나 값이 None인 필수 항목이 하나라도 있으면 missing_required입니다.
    # 빈 문자열은 다음 텍스트 검사에서 따로 거릅니다.
    if any(key not in row or row[key] is None for key in required):
        return "missing_required"

    # 숫자로 검사할 필드를 집합에 모읍니다.
    # 그 외 필수 필드는 문자열이며 공백만 있는 값도 허용하지 않습니다.
    numeric = {"quantity", "unit_price_krw", "amount_krw"}
    for key in required:
        if key not in numeric and (
            not isinstance(row[key], str) or not row[key].strip()
        ):
            return "invalid_text"

    # get("metadata", {})는 키가 없을 때 빈 딕셔너리를 기본값으로 줍니다.
    # 키가 존재하면서 None이면 기본값으로 바꾸지 않으므로 invalid_metadata가 됩니다.
    if not isinstance(row.get("metadata", {}), dict):
        return "invalid_metadata"
    # 시간 문자열 파싱 실패를 오류 사유로 바꿔 반환하여, 한 행의 형식 오류를 배치 전체 예외와 구분합니다.
    try:

        # 주문 검증 순서: 시각 → 수량 → 단가 → 채널입니다.
        # 첫 오류에서 return하므로 한 행의 모든 오류를 한 번에 모으는 검증기는 아닙니다.
        if source == "orders":
            timestamp(row["ordered_at"])
            if not positive_integer(row["quantity"], 100):
                return "invalid_quantity"
            if not positive_integer(row["unit_price_krw"], 10**9):
                return "invalid_price"
            if row["channel"] not in ("web", "app", "market"):
                return "invalid_channel"

        # 결제 종류는 capture(수납)와 refund(환불)만 허용합니다.
        # 환불도 양수 금액을 저장하며, 집계할 때 종류에 따라 빼 줍니다.
        elif source == "payments":
            if row["kind"] not in ("capture", "refund"):
                return "invalid_payment_kind"
            if not positive_integer(row["amount_krw"]):
                return "invalid_amount"

            # 실제 발생보다 먼저 수신할 수는 없다는 시간 규칙입니다.
            # 시간대가 달라도 aware datetime끼리는 실제 순간을 기준으로 비교합니다.
            if timestamp(row["received_at"]) < timestamp(row["occurred_at"]):
                return "negative_delivery_lag"
        else:

            # 배송의 약속 시각은 형식만 확인합니다.
            # 완료가 약속보다 늦은 것은 배송 지연이라는 업무 지표이며 데이터 형식 오류로 버리지 않습니다.
            timestamp(row["promised_at"])
            if timestamp(row["received_at"]) < timestamp(row["delivered_at"]):
                return "negative_delivery_lag"

    # 시각 파싱에서 발생할 수 있는 오류를 문자열 사유로 바꿉니다.
    # 호출자는 이 반환값을 격리 기록에 넣고 다른 행 처리를 계속할 수 있습니다.
    except (ValueError, TypeError, OverflowError):
        return "invalid_timestamp"

    # 모든 검사를 통과한 경로입니다.
    # 호출자의 if not reason은 이 None을 정상으로 해석합니다.
    return None


# 호환 문자·대소문자·법인 표기·공백을 정리하되 숫자는 보존합니다. 결과가 같아도 실제 동일 사업자라는 증거는 아닙니다.
def normalize_seller(name):

    # NFKC는 호환 문자를 통일하고 casefold는 대소문자 차이를 줄입니다.
    # 예: 전각 문자나 일부 법인 기호의 표기를 비교하기 쉬운 형태로 바꿉니다.
    name = unicodedata.normalize("NFKC", name).casefold()
    for marker in ["주식회사", "(주)", "㈜"]:
        name = name.replace(marker, "")

    # r"\s+"는 공백 문자가 한 번 이상 연속된 부분입니다. 이를 빈 문자열로 바꿉니다.
    # 예: "상점 01" → "상점01". 판매자를 구별하는 숫자는 제거하지 않습니다.
    return re.sub(r"\s+", "", name)
