# pytest는 함수 이름 test_와 fixture 인자를 보고 테스트와 준비 단계를 연결합니다.
import pytest
from shopscope.quality import validate, normalize_seller
from shopscope.benchmark import histogram_percentile


# 유효한 기본 주문에 원하는 변경만 덮어씌워 테스트 입력을 만듭니다. 각 테스트가 확인할 조건을 드러내는 헬퍼입니다.
def order(**changes):

    # 매번 유효한 기본 주문을 새로 만듭니다.
    # 호출 예: order(quantity=0)는 다른 조건은 정상으로 두고 수량만 바꿉니다.
    row = dict(
        order_id="O1",
        customer_id="C1",
        product_id="P1",
        channel="app",
        ordered_at="2026-08-01T01:00:00+09:00",
        quantity=2,
        unit_price_krw=1000,
    )

    # **changes는 키워드 인자로 받은 변경 사항 딕셔너리입니다.
    # 기본 row를 복사하고 같은 키의 값을 변경 사항으로 덮어씁니다.
    return dict(row, **changes)


# 매개변수화로 여섯 경계/타입 사례를 따로 실행합니다. 숫자처럼 보이는 문자열과 bool도 유효한 수량이 아닙니다.

# 하나의 테스트 함수를 입력마다 별도 사례로 실행합니다.
# 0/음수/불리언/소수/문자열/상한 초과가 모두 같은 오류 사유를 반환해야 합니다.
@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2", 101])
def test_invalid_quantity(quantity):
    assert validate("orders", order(quantity=quantity)) == "invalid_quantity"


# 시각 형식이 맞더라도 시간대가 없으면 거부해야 합니다. 지역에 따라 같은 문자열의 실제 순간이 달라지기 때문입니다.
def test_timezone_required():
    assert (
        validate("orders", order(ordered_at="2026-08-01T01:00:00"))
        == "invalid_timestamp"
    )


# 정상 주문의 반환값은 True가 아니라 오류 사유가 없다는 뜻의 None입니다.
def test_valid_order():
    assert validate("orders", order()) is None


# 법인 표기와 공백만 정리하고 서로 다른 숫자는 보존하는지 확인합니다. 과도한 정규화로 다른 판매자를 합치지 않게 합니다.
def test_name_normalization_preserves_identity_digits():
    assert normalize_seller("㈜ 상점 01") == normalize_seller("상점01(주)")
    assert normalize_seller("상점01") != normalize_seller("상점02")


# 빈도표 {1:2,5:1,10:1}은 [1,1,5,10]입니다. 중앙값 3과 빈 표본의 None 반환을 확인합니다.
def test_weighted_percentile_matches_known_sample():

    # 표본을 풀면 [1, 1, 5, 10]이므로 가운데 1과 5의 평균은 3입니다.
    # 빈 입력은 계산할 위치가 없으므로 None이어야 합니다.
    assert histogram_percentile({1: 2, 5: 1, 10: 1}, 50) == 3
    assert histogram_percentile({}, 99) is None
