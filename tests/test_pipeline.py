# pytest는 함수 이름 test_와 fixture 인자를 보고 테스트와 준비 단계를 연결합니다.
import json
import threading
from http.server import ThreadingHTTPServer
import pytest
from shopscope.source import handler_factory
from shopscope.generate import generate, write_json, digest, encode
from shopscope.collect import collect_day, collect_catalog
from shopscope.pipeline import (
    load_catalog,
    load_day,
    verify_truth,
    put_row,
    check_finance,
    refresh_mart,
)
from shopscope.db import connect


# 임시 폴더에 3일치 주문을 만들고 실제 로컬 HTTP 서버에서 수집합니다. port=0은 사용 가능한 포트를 OS가 선택하게 합니다.
@pytest.fixture
def prepared(tmp_path, db):

    # tmp_path는 pytest가 제공하는 테스트별 임시 폴더입니다.
    # 합성 입력은 여기에 만들고 DB는 db fixture가 준비한 임시 DB만 사용합니다.
    truth = generate(days=3, per_day=40, root=tmp_path)

    # 실제 네트워크 요청/응답 경로를 검증하는 로컬 서버입니다.
    # faults=True이므로 수집기의 일시 오류 재시도까지 함께 실행됩니다.
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_factory(tmp_path, faults=True)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    load_catalog(collect_catalog(base, tmp_path), db)
    for day in truth["dates"]:
        collect_day(day, base, tmp_path)
    server.shutdown()
    server.server_close()
    thread.join()

    # prepared fixture의 반환값을 각 테스트가 root, dsn, truth로 언패킹합니다.
    # 수집까지만 준비하고 load_day는 각 테스트가 필요한 순서/실패 조건으로 호출합니다.
    return tmp_path, db, truth


# 전체 적재 후 같은 배치를 재실행합니다. 합계 불변과 잘못된 주문 3건+고아 결제 1건의 격리를 확인합니다.
def test_end_to_end_truth_and_replay(prepared):
    root, dsn, truth = prepared
    for day in truth["dates"]:
        load_day(day, root, dsn)

    # 먼저 모든 날짜 적재 결과를 검증합니다.
    # 아래 반복은 똑같은 입력을 다시 적재한 뒤 skipped와 합계 불변을 동시에 확인합니다.
    before = verify_truth(root, dsn)
    for day in truth["dates"]:
        assert load_day(day, root, dsn)["skipped"]
    assert verify_truth(root, dsn) == before
    with connect(dsn) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) n FROM quarantine WHERE status='open'"
            ).fetchone()["n"]
            == 4
        )


# 커밋 직전 장애를 주입합니다. 주문/완료 기록은 취소되고 실패 이력은 남으며 재시도가 성공해야 합니다.
def test_failure_rolls_back_checkpoint_and_ledger(prepared):
    root, dsn, truth = prepared

    # pytest.raises는 블록에서 지정한 예외가 나와야 통과합니다.
    # 예외가 없거나 다른 종류이면 테스트 실패입니다. match는 메시지에 injected가 있는지도 검사합니다.
    with pytest.raises(RuntimeError, match="injected"):
        load_day(truth["dates"][0], root, dsn, fail_before_commit=True)
    with connect(dsn) as conn:
        assert conn.execute("SELECT count(*) n FROM orders").fetchone()["n"] == 0
        assert (
            conn.execute("SELECT count(*) n FROM processed_batches").fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) n FROM pipeline_runs WHERE status='failed'"
            ).fetchone()["n"]
            == 1
        )
    assert load_day(truth["dates"][0], root, dsn)["inserted"] > 0


# 늦은 환불 전후에 첫 주문일 마트 값을 비교합니다. 수신 당일이 아닌 원래 주문일의 순수납액이 줄어야 합니다.
def test_late_refund_revises_original_order_day(prepared):
    root, dsn, truth = prepared
    for day in truth["dates"][:4]:
        load_day(day, root, dsn)
    with connect(dsn) as conn:
        before = conn.execute(
            "SELECT net_collected_krw FROM mart_daily WHERE business_date=%s",
            (truth["dates"][0],),
        ).fetchone()["net_collected_krw"]
    load_day(truth["dates"][4], root, dsn)
    with connect(dsn) as conn:
        after = conn.execute(
            "SELECT net_collected_krw FROM mart_daily WHERE business_date=%s",
            (truth["dates"][0],),
        ).fetchone()["net_collected_krw"]

    # 이 테스트는 환불 수신으로 과거 주문일의 순수납액이 줄어든 사실을 확인합니다.
    # 전체 정확한 환불 금액은 다른 정답 검증 테스트가 함께 확인합니다.
    assert after < before


# manifest를 바꾸지 않고 raw 파일만 수정해 checksum 검증이 DB 적재 전에 거부하는지 확인합니다.
def test_tampered_file_is_rejected(prepared):
    root, dsn, truth = prepared
    file = root / "raw" / truth["dates"][0] / "orders.jsonl"

    # 파일 내용만 추가하고 manifest의 checksum은 유지해 불일치를 만듭니다.
    # 이후 load_day가 checksum 오류를 내야 하며 손상된 데이터를 받아들이면 테스트가 실패합니다.
    file.write_text(file.read_text() + "{}\n")
    with pytest.raises(ValueError, match="checksum"):
        load_day(truth["dates"][0], root, dsn)


# 동일 결제 ID의 금액만 변경합니다. 격리되어야 하고 이미 저장한 결제 금액은 유지되어야 합니다.
def test_conflicting_id_does_not_overwrite(prepared):
    root, dsn, truth = prepared
    load_day(truth["dates"][0], root, dsn)
    row = json.loads(
        (root / "raw" / truth["dates"][0] / "payments.jsonl")
        .read_text()
        .splitlines()[0]
    )
    with connect(dsn) as conn:
        before = conn.execute(
            "SELECT amount_krw FROM payments WHERE event_id=%s", (row["event_id"],)
        ).fetchone()["amount_krw"]

        # ID는 그대로 두고 내용만 바꿉니다.
        # 이 상황은 단순 재전송이 아니라 충돌이므로 put_row는 quarantined를 반환해야 합니다.
        row["amount_krw"] += 100
        assert put_row(conn, "payments", row, "test") == "quarantined"
        assert (
            conn.execute(
                "SELECT amount_krw FROM payments WHERE event_id=%s", (row["event_id"],)
            ).fetchone()["amount_krw"]
            == before
        )


# 형식상 정상인 과다 환불을 삽입합니다. 주문별 합계 검증이 예외를 내고 해당 INSERT도 롤백되어야 합니다.
def test_overrefund_fails_financial_gate(prepared):
    root, dsn, truth = prepared
    load_day(truth["dates"][0], root, dsn)
    row = json.loads(
        (root / "raw" / truth["dates"][0] / "payments.jsonl")
        .read_text()
        .splitlines()[0]
    )

    # 새 ID를 주어 ID 충돌 검사를 피하고 큰 환불을 만듭니다.
    # 행 형식 검증과 주문 합계 검증이 서로 다른 계층임을 드러내는 입력입니다.
    row.update(event_id="OVER-REFUND", kind="refund", amount_krw=10**9)
    with pytest.raises(ValueError, match="환불"):
        with connect(dsn) as conn:
            put_row(conn, "payments", row, "test")
            check_finance(conn)
    with connect(dsn) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM payments WHERE event_id='OVER-REFUND'"
            ).fetchone()
            is None
        )


# 결제를 주문보다 먼저 넣어 격리한 뒤 부모 주문을 적재하고 재검사합니다. 복구된 격리 기록은 resolved가 됩니다.
def test_orphan_can_be_reprocessed_after_parent(prepared):
    root, dsn, truth = prepared
    order = json.loads(
        (root / "raw" / truth["dates"][0] / "orders.jsonl").read_text().splitlines()[0]
    )
    payment = json.loads(
        (root / "raw" / truth["dates"][0] / "payments.jsonl")
        .read_text()
        .splitlines()[0]
    )
    with connect(dsn) as conn:

        # 부모가 없는 결제 → 부모 주문 추가 → 결제 재시도의 순서입니다.
        # 같은 연결 안에서 이전 INSERT를 볼 수 있으므로 부모 추가 후 재시도가 통과합니다.
        assert put_row(conn, "payments", payment, "early") == "quarantined"
        assert put_row(conn, "orders", order, "parent") == "inserted"
        assert put_row(conn, "payments", payment, "retry") == "inserted"
        assert (
            conn.execute(
                "SELECT status FROM quarantine WHERE payload->>'event_id'=%s",
                (payment["event_id"],),
            ).fetchone()["status"]
            == "resolved"
        )


# 분할 결제가 있는 주문도 기대 금액을 한 번만 세어야 합니다. 원장 뷰의 JOIN으로 GMV가 부풀지 않는지 확인합니다.
def test_split_payment_join_does_not_multiply_gmv(prepared):
    root, dsn, truth = prepared
    for day in truth["dates"]:
        load_day(day, root, dsn)
    with connect(dsn) as conn:
        got = conn.execute(
            "SELECT SUM(expected_krw) total FROM order_finance"
        ).fetchone()["total"]

        # 생성기는 정상 주문 기대 금액만큼 결제를 만들므로 두 합계가 같아야 합니다.
        # 분할 결제 JOIN 때문에 주문이 중복되어 세어지면 이 검사가 실패합니다.
        assert got == truth["captured_krw"]


# 정확히 30분 간격은 같은 세션, 31분 간격은 새 세션입니다. 조건이 >=가 아니라 >임을 검증합니다.
def test_session_30_minute_boundary(db):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO click_events VALUES ('1','C','2026-08-01T00:00:00Z','view'),('2','C','2026-08-01T00:30:00Z','cart'),('3','C','2026-08-01T01:01:00Z','buy')"
        )
        assert [
            r["session_id"]
            for r in conn.execute(
                "SELECT session_id FROM customer_sessions ORDER BY event_id"
            )
        ] == [1, 1, 2]
