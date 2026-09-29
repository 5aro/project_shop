"""7. 데이터 파이프라인 핵심
수집된 데이터를 검증하고 데이터베이스에 적재하는 프로젝트의 핵심 파이프라인입니다."""


# 이 모듈의 입력은 collect가 저장한 raw 파일이고 출력은 PostgreSQL 원장/마트와 실행 지표입니다.
# Jsonb는 Python 객체를 PostgreSQL JSONB 값으로 전달하는 어댑터입니다.
# digest는 파일 자체가 아니라 Python 객체를 일관된 JSON 표현으로 바꾼 후 해시합니다.
import hashlib
import json
import time
import uuid
from datetime import date
from pathlib import Path
from psycopg.types.json import Jsonb
from .db import connect
from .config import DATA
from .generate import digest
from .quality import validate

# 삽입 열의 허용 목록이자 처리 순서입니다. 상품은 미리 적재하고, 여기서는 부모 주문을 결제/배송보다 먼저 처리합니다.

# 각 값은 (기본키 이름, INSERT할 열 목록)입니다.
# 예: payments → (event_id, [event_id, order_id, kind, ...])
# Python 딕셔너리는 삽입 순서를 유지하므로 아래 반복에서 orders가 먼저 처리됩니다.
TABLES = {
    "orders": (
        "order_id",
        [
            "order_id",
            "customer_id",
            "product_id",
            "channel",
            "ordered_at",
            "quantity",
            "unit_price_krw",
            "metadata",
        ],
    ),
    "payments": (
        "event_id",
        [
            "event_id",
            "order_id",
            "kind",
            "amount_krw",
            "occurred_at",
            "received_at",
            "metadata",
        ],
    ),
    "shipments": (
        "event_id",
        [
            "event_id",
            "order_id",
            "carrier",
            "promised_at",
            "delivered_at",
            "received_at",
        ],
    ),
}


# 상품 ID가 있으면 속성을 갱신하는 upsert입니다. 제목/설명이 바뀌면 문자 해시 임베딩을 비워 재생성을 유도합니다.
def load_catalog(products, dsn=None):
    with connect(dsn) as conn:

        # 상품 목록의 각 딕셔너리를 p로 받습니다.
        # ON CONFLICT(product_id)는 같은 상품이 이미 있으면 INSERT 대신 DO UPDATE를 수행합니다.
        # excluded는 이번에 넣으려 했던 새 값들을 가리키는 PostgreSQL 이름입니다.
        for p in products:
            conn.execute(
                """INSERT INTO products(product_id,title,category,seller_id,price_krw,description,metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(product_id) DO UPDATE SET
                title=excluded.title,category=excluded.category,seller_id=excluded.seller_id,
                price_krw=excluded.price_krw,description=excluded.description,metadata=excluded.metadata,
                embedding=CASE WHEN (products.title,products.description) IS DISTINCT FROM (excluded.title,excluded.description) THEN NULL ELSE products.embedding END,
                embedding_model=CASE WHEN (products.title,products.description) IS DISTINCT FROM (excluded.title,excluded.description) THEN NULL ELSE products.embedding_model END""",
                [

                    # 목록 컴프리헨션으로 SQL의 %s 순서에 맞는 값을 만듭니다.
                    # 일반 열은 그대로, metadata만 Jsonb로 변환합니다.
                    # 상품 텍스트가 달라질 때 embedding을 NULL로 만드는 조건은 SQL 내부 CASE에 있습니다.
                    p[k] if k != "metadata" else Jsonb(p[k])
                    for k in [
                        "product_id",
                        "title",
                        "category",
                        "seller_id",
                        "price_krw",
                        "description",
                        "metadata",
                    ]
                ],
            )


# 원문 보존 → 형식/참조 검증 → 중복/충돌 판단 → 원장 적재 순서입니다. 반환 문자열은 load_day의 지표 키로 쓰입니다.
def put_row(conn, source, row, batch):

    # 행 전체 내용으로 h를 계산합니다.
    # 같은 ID여도 금액이나 metadata가 바뀌면 다른 해시가 됩니다.
    # 반대로 키의 나열 순서만 바뀌면 정렬된 JSON으로 해시하므로 같은 값입니다.
    h = digest(row)
    conn.execute(
        "INSERT INTO raw_events VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (source, h, Jsonb(row), batch),
    )
    # raw_events 저장도 현재 DB 트랜잭션에 속합니다. 배치 실패로 롤백되면 이 기록도 취소되지만 디스크 raw 파일은 남습니다.

    # None이면 형식 검사를 통과했고, 문자열이면 격리할 사유입니다.
    # 이 단계는 DB 조회 없이 검사 가능한 수량·타입·시각 등을 확인합니다.
    reason = validate(source, row)
    if not reason:

        # 형식이 정상이어도 부모 데이터가 없는 경우가 있습니다.
        # 주문은 products에 product_id가 있어야 하고, 결제/배송은 orders에 order_id가 있어야 합니다.
        # SELECT 1은 전체 행 대신 존재 여부를 확인하기 위한 상수 조회입니다.
        if source == "orders":
            if not conn.execute(
                "SELECT 1 FROM products WHERE product_id=%s", (row["product_id"],)
            ).fetchone():
                reason = "unknown_product"
        elif not conn.execute(
            "SELECT 1 FROM orders WHERE order_id=%s", (row["order_id"],)
        ).fetchone():
            reason = "orphan_order"

    # 튜플 언패킹으로 기본키 이름과 열 목록을 나눠 받습니다.
    # source는 TABLES의 허용된 이름이어야 하므로 외부 입력을 임의 테이블명으로 쓰지 않습니다.
    key, columns = TABLES[source]
    if not reason:

        # 이미 같은 이벤트 ID가 저장되어 있는지 payload_hash만 조회합니다.
        # fetchone()은 결과가 없으면 None이므로 아래 if previous로 존재 여부를 판단합니다.
        previous = conn.execute(
            f"SELECT payload_hash FROM {source} WHERE {key}=%s", (row[key],)
        ).fetchone()
        # 같은 ID+같은 해시 = 재전송(duplicate), 같은 ID+다른 해시 = 충돌(quarantine)입니다. 원장 금액을 덮어쓰지 않습니다.
        if previous:

            # 이미 받은 내용이면 다시 INSERT하지 않고 duplicate를 반환합니다.
            # 해시가 다르면 기존 원장을 수정하지 않고 conflicting_event_id 사유를 설정합니다.
            if previous["payload_hash"] == h:
                return "duplicate"
            reason = "conflicting_event_id"

    # 오류 행을 격리 테이블에 원문 그대로 보존합니다.
    # source+payload_hash가 이미 있으면 사유를 갱신하고 open 상태로 되돌립니다.
    # 이 함수는 예외 대신 quarantined를 반환하여 다음 행을 처리할 수 있게 합니다.
    if reason:
        conn.execute(
            """INSERT INTO quarantine(source,payload_hash,payload,reason,first_batch)
          VALUES (%s,%s,%s,%s,%s) ON CONFLICT(source,payload_hash) DO UPDATE SET reason=excluded.reason,status='open' """,
            (source, h, Jsonb(row), reason, batch),
        )
        return "quarantined"
    # Python dict를 Jsonb로 감싸 PostgreSQL JSONB 열에 전달합니다. 열 목록 순서와 값 목록 순서는 반드시 일치해야 합니다.

    # INSERT 값은 columns 순서로 꺼내고 끝에 payload_hash를 추가합니다.
    # metadata가 생략되어 있으면 빈 객체를 넣습니다.
    # 예: columns가 [event_id, order_id]라면 values도 [이벤트ID, 주문ID, 해시] 순서입니다.
    values = [Jsonb(row.get(k, {})) if k == "metadata" else row[k] for k in columns] + [
        h
    ]

    # 열 이름 목록과 자리표시자 목록을 별도로 만듭니다.
    # names는 "event_id,order_id,...,payload_hash", marks는 "%s,%s,..." 형태입니다.
    # 실제 값은 문자열 안에 합치지 않고 execute의 두 번째 인자로 전달합니다.
    names = ",".join(columns + ["payload_hash"])
    marks = ",".join(["%s"] * len(values))
    # 식별자는 TABLES 상수에서만 가져옵니다. 데이터 값은 모두 바인딩합니다.
    conn.execute(f"INSERT INTO {source}({names}) VALUES ({marks})", values)
    conn.execute(
        "UPDATE quarantine SET status='resolved' WHERE source=%s AND payload_hash=%s",
        (source, h),
    )

    # 적재를 마치면 앞서 같은 원문이 격리되었을 경우 resolved로 바뀝니다.
    # 이 반환값은 호출자가 신규 적재 건수를 올리는 데 사용합니다.
    # 함수 자체는 commit하지 않으며 배치 단위 연결에서 최종 확정합니다.
    return "inserted"


# 행 하나로는 알 수 없는 주문별 합계와 시간 순서를 검사합니다. 예외가 발생하면 호출자의 트랜잭션 전체가 롤백됩니다.
def check_finance(conn):
    invalid = conn.execute(
        "SELECT COUNT(*) AS n FROM order_finance WHERE refunded_krw>captured_krw"
    ).fetchone()["n"]

    # COUNT(*) 결과가 0이면 조건에 맞는 문제가 없다는 뜻입니다.
    # 0보다 크면 ValueError를 던져 이 연결에서 수행한 모든 배치 변경을 취소하게 합니다.
    if invalid:
        raise ValueError(
            f"환불 합계가 결제액보다 큰 주문 {invalid}건: 원장/마트/체크포인트 롤백"
        )
    invalid = conn.execute(
        """SELECT count(*) n FROM payments p JOIN orders o USING(order_id)
        WHERE p.occurred_at<o.ordered_at"""
    ).fetchone()["n"]
    if invalid:
        raise ValueError("주문보다 이른 결제 이벤트")


# 주문별 원장 뷰를 한국 시각의 주문일로 집계합니다. 수신일과 주문일이 달라 늦은 환불은 과거 날짜의 금액을 바꿉니다.
def refresh_mart(conn):
    # 소규모 포트폴리오에서는 전체 재집계로 늦은 환불의 과거 주문일 반영을 보장합니다.
    # 원천/원장은 증분, 마트는 전체 재생성입니다. 운영 규모에서는 영향 날짜만 교체합니다.

    # 이후 INSERT와 같은 트랜잭션에서 실행하므로 중간 실패 시 삭제도 롤백됩니다.
    # 테이블 구조를 삭제하는 DROP이 아니라 요약 행만 비우는 DELETE입니다.
    conn.execute("DELETE FROM mart_daily")

    # ordered_at을 한국 시각의 날짜로 바꾸고 그 날짜별로 합산합니다.
    # COUNT(*) FILTER(WHERE late)는 지연된 주문만 세는 조건부 집계입니다.
    # 원장 뷰가 주문당 한 행이므로 결제 이벤트 개수가 아니라 주문 개수를 셉니다.
    conn.execute("""INSERT INTO mart_daily
      SELECT (ordered_at AT TIME ZONE 'Asia/Seoul')::date, COUNT(*),SUM(expected_krw),
       SUM(captured_krw),SUM(refunded_krw),SUM(net_collected_krw),
       COUNT(*) FILTER(WHERE delivered),COUNT(*) FILTER(WHERE late),COUNT(*) FILTER(WHERE mismatch)
      FROM order_finance GROUP BY 1""")


# 저장된 raw 파일을 검증한 후 하나의 배치를 적재합니다. 원장·마트·완료 체크포인트를 같은 트랜잭션에서 확정합니다.
def load_day(day, root=DATA, dsn=None, fail_before_commit=False):

    # 입력 예: load_day("2026-08-01")
    # 날짜를 확인한 뒤 data/raw/2026-08-01 폴더에서 manifest와 세 JSONL 파일을 읽습니다.
    # 수신 날짜 파티션에 과거 주문의 환불이 들어 있을 수 있습니다.
    day = str(date.fromisoformat(day))
    partition = Path(root) / "raw" / day
    manifest = json.loads((partition / "manifest.json").read_text())

    # 잘못된 날짜의 manifest를 붙였거나 지원하지 않는 버전이면 적재하지 않습니다.
    # manifest는 collect 단계와 load 단계가 공유하는 데이터 계약입니다.
    if manifest["business_date"] != day:
        raise ValueError("매니페스트 날짜 불일치")
    if manifest.get("schema_version") != 1:
        raise ValueError("지원하지 않는 스키마 버전")
    # 파일 해시와 manifest 전체 해시는 역할이 다릅니다. 전자는 원본 파일, 후자는 배치 명세 변경을 감지합니다.
    manifest_hash = digest(manifest)
    batch = manifest["batch_id"]

    # 원천 이름별로 읽은 행 목록을 보관합니다.
    # 예: {"orders": [주문1, 주문2], "payments": [...], "shipments": [...]}
    # 현재 구현은 하루 데이터를 메모리에 모두 올리는 소규모 학습용 방식입니다.
    rows = {}

    # 각 원천 파일을 원본 bytes로 읽고 manifest의 파일 해시와 비교합니다.
    # 해시가 맞은 다음 줄별 JSON을 해석하며, 빈 줄은 if line으로 건너뜁니다.
    # 건수도 확인하여 manifest와 실제 입력이 일치하는지 이중으로 검사합니다.
    for source in TABLES:
        payload = (partition / f"{source}.jsonl").read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest["files"][source]["sha256"]:
            raise ValueError("원천 checksum 불일치")
        rows[source] = [json.loads(line) for line in payload.splitlines() if line]
        if len(rows[source]) != manifest["files"][source]["rows"]:
            raise ValueError("매니페스트 건수 불일치")

    # rows.values()는 원천별 목록들입니다. map(len, ...)이 각 목록 길이를 구하고 sum이 더합니다.
    # 예: 주문 10 + 결제 12 + 배송 3 = rows_seen 25건입니다.
    total = sum(map(len, rows.values()))
    if total == 0:
        raise ValueError("빈 배치: 실패로 처리")
    # 배치 ID는 재실행해도 같을 수 있지만 run_id는 매 시도마다 새로 만듭니다. 처리 단위와 실행 이력을 분리합니다.
    run = uuid.uuid4().hex

    # perf_counter는 경과 시간 측정용 시계입니다.
    # 달력 시각이 필요한 실행 이력은 DB의 now()로, 소요 시간은 두 perf_counter 값의 차이로 기록합니다.
    started = time.perf_counter()
    # running 이력은 먼저 별도 커밋합니다. 본 적재가 롤백되어도 실패 상태로 갱신할 대상이 남아야 하기 때문입니다.
    with connect(dsn) as conn:
        conn.execute(
            "INSERT INTO pipeline_runs(run_id,business_date,status) VALUES (%s,%s,%s)",
            (run, day, "running"),
        )

    # metrics는 이번 실행의 집계 딕셔너리입니다.
    # inserted=새 원장 행, duplicate=같은 내용 재전송, quarantined=현재 입력 중 격리,
    # recovered=이전 격리 기록을 재검사해 복구한 행입니다. 서로 같은 모집단의 비율이 아닙니다.
    metrics = {
        "rows_seen": total,
        "inserted": 0,
        "duplicate": 0,
        "quarantined": 0,
        "recovered": 0,
    }
    try:
        with connect(dsn) as conn:
            # 일괄 쓰기를 직렬화해 서로 다른 날짜의 동시 실행도 순서 경쟁을 피합니다.

            # 같은 키 731842를 사용하는 적재끼리는 한 번에 하나만 쓰도록 대기합니다.
            # 트랜잭션이 끝나면 잠금이 자동 해제됩니다.
            # 이 잠금 규칙을 사용하지 않는 다른 프로그램의 쓰기까지 막는 것은 아닙니다.
            conn.execute("SELECT pg_advisory_xact_lock(731842)")
            previous = conn.execute(
                "SELECT manifest_hash FROM processed_batches WHERE batch_id=%s",
                (batch,),
            ).fetchone()

            # processed_batches에서 이미 처리한 배치를 찾은 경로입니다.
            # 같은 batch_id인데 manifest_hash가 달라졌다면 잘못된 변경으로 거부합니다.
            # 명세도 같다면 skipped=True만 기록하고 원장을 다시 적재하지 않습니다.
            if previous:
                if previous["manifest_hash"] != manifest_hash:
                    raise ValueError("같은 batch_id의 manifest 변경")
                metrics["skipped"] = True
            else:

                # items()는 (원천 이름, 행 목록)을 반환합니다.
                # 각 행에 put_row를 호출하면 inserted/duplicate/quarantined 중 하나가 돌아옵니다.
                # 그 문자열을 metrics의 키로 사용해 해당 카운터를 1 올립니다.
                for source, items in rows.items():
                    for row in items:
                        metrics[put_row(conn, source, row, batch)] += 1
                # 부모 주문보다 먼저 온 자식 이벤트는 다음 배치에서 자동 재검사합니다.

                # 형식 자체가 틀린 격리 행은 자동 복구 대상이 아닙니다.
                # 부모 주문이 없다는 이유(orphan_order)로 열린 기록만 다시 가져옵니다.
                # 이번 배치에서 부모가 들어왔으면 같은 put_row 검증을 통과해 저장될 수 있습니다.
                pending = conn.execute(
                    "SELECT source,payload,first_batch FROM quarantine WHERE status='open' AND reason='orphan_order'"
                ).fetchall()
                for item in pending:
                    if (
                        put_row(
                            conn, item["source"], item["payload"], item["first_batch"]
                        )
                        == "inserted"
                    ):
                        metrics["recovered"] += 1
                # 5%는 이 합성 실습의 배치 중단 기준입니다. 분자는 현재 배치 처리 중 격리된 건수이며 전체 미해결 격리 건수와 다릅니다.
                if metrics["quarantined"] / total > 0.05:
                    raise ValueError("배치 격리율 5% 초과")

                # 개별 행을 모두 처리한 후 주문별 결제/환불 합계를 검사합니다.
                # 이를 통과해야 마트를 갱신합니다. 행 단위로 정상인 환불도 합계가 과다하면 여기서 실패합니다.
                check_finance(conn)
                refresh_mart(conn)
                # 의도적인 장애 지점입니다. 원장과 마트까지 바꾼 뒤 실패시켜 체크포인트와 함께 롤백되는지 테스트합니다.
                if fail_before_commit:
                    raise RuntimeError("injected failure before commit")
                conn.execute(
                    "INSERT INTO processed_batches(batch_id,business_date,manifest_hash,rows_seen) VALUES (%s,%s,%s,%s)",
                    (batch, day, manifest_hash, total),
                )

            # 성공 경로에서 경과 시간을 기록합니다. 이미 처리한 배치를 건너뛴 실행도 success로 남습니다.
            # 이 UPDATE와 완료 체크포인트가 같은 트랜잭션 안에서 확정됩니다.
            metrics["elapsed_seconds"] = round(time.perf_counter() - started, 4)
            conn.execute(
                "UPDATE pipeline_runs SET status='success',ended_at=now(),metrics=%s WHERE run_id=%s",
                (Jsonb(metrics), run),
            )

            # 현재 입력에서 격리가 발생했지만 5% 기준을 넘지 않았다면 경고를 남기고 성공할 수 있습니다.
            # quarantine:날짜를 알림 키로 쓰므로 같은 날짜 경고가 반복되면 occurrences를 증가시킵니다.
            if metrics["quarantined"]:
                conn.execute(
                    """INSERT INTO alerts(alert_key,severity,message) VALUES (%s,'WARN',%s)
                   ON CONFLICT(alert_key) DO UPDATE SET occurrences=alerts.occurrences+1,last_seen=now()""",
                    (
                        f"quarantine:{day}",
                        f'{day}: {metrics["quarantined"]} rows quarantined',
                    ),
                )

        # **metrics는 딕셔너리의 키/값을 결과 딕셔너리 안으로 펼칩니다.
        # 예: {run_id: ..., business_date: ..., rows_seen: ..., inserted: ...} 형태입니다.
        return dict(run_id=run, business_date=day, **metrics)
    # 본 적재 트랜잭션이 종료된 후 새 연결로 실패 이력을 남깁니다. 마지막 raise로 원래 실패를 호출자에게 다시 전달합니다.

    # with connect 블록에서 빠져나오며 원장 변경이 먼저 롤백됩니다.
    # 새 연결로 실행을 failed로 바꾸고 CRITICAL 알림을 기록한 후 bare raise로 원래 예외를 재전달합니다.
    # 오류 메시지는 [:500]으로 길이를 제한합니다.
    except Exception as exc:
        with connect(dsn) as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status='failed',ended_at=now(),error=%s WHERE run_id=%s",
                (str(exc)[:500], run),
            )
            conn.execute(
                """INSERT INTO alerts(alert_key,severity,message) VALUES (%s,'CRITICAL',%s)
                ON CONFLICT(alert_key) DO UPDATE SET occurrences=alerts.occurrences+1,last_seen=now(),message=excluded.message""",
                (f"load_failed:{day}", str(exc)[:500]),
            )
        raise


# 클릭 이벤트를 묶어서 INSERT합니다. 이벤트 기본키 충돌은 무시하므로 같은 파일을 재실행해도 행이 늘지 않습니다.
def load_clicks(root=DATA, dsn=None):
    rows = json.loads((Path(root) / "source/clicks.json").read_text())
    with connect(dsn) as conn:

        # cursor는 SQL 결과와 여러 실행을 다루는 객체입니다.
        # executemany는 같은 INSERT 템플릿에 여러 행의 값을 차례로 적용합니다.
        # with가 끝나면 cursor가 닫히고 바깥 연결의 트랜잭션이 커밋됩니다.
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO click_events VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                [
                    (r["event_id"], r["customer_id"], r["occurred_at"], r["action"])
                    for r in rows
                ],
            )


# 생성 시 누적해 둔 정답과 DB 집계, 마트 합계를 대조합니다. 모든 개별 필드가 정확하다는 뜻이 아니라 명시된 합계 검증입니다.
def verify_truth(root=DATA, dsn=None):

    # 생성기가 정상 거래를 만들 때 계산해 둔 정답 합계를 읽습니다.
    # 이상 행이나 중복을 그대로 더한 raw 합계가 아니라 의도된 정상 거래의 합계입니다.
    truth = json.loads((Path(root) / "source/truth.json").read_text())
    with connect(dsn) as conn:
        actual = conn.execute(
            """SELECT COUNT(*) AS orders,COALESCE(SUM(captured_krw),0) AS captured_krw,
             COALESCE(SUM(refunded_krw),0) AS refunded_krw,COALESCE(SUM(net_collected_krw),0) AS net_collected_krw
             FROM order_finance"""
        ).fetchone()

        # 조회한 실제 합계의 각 키를 정답의 같은 키와 비교합니다.
        # 결과는 {"orders": True, "captured_krw": True, ...} 형태입니다.
        checks = {key: actual[key] == truth[key] for key in actual}
        mart = conn.execute(
            "SELECT COALESCE(SUM(net_collected_krw),0) n FROM mart_daily"
        ).fetchone()["n"]
        checks["mart_matches_ledger"] = mart == actual["net_collected_krw"]
        checks["no_negative_net"] = (
            conn.execute(
                "SELECT COUNT(*) n FROM order_finance WHERE net_collected_krw<0"
            ).fetchone()["n"]
            == 0
        )

    # all은 모든 원소가 참일 때만 True입니다.
    # 어느 항목이든 틀리면 실제/기대/개별 비교 결과를 담아 AssertionError를 냅니다.
    # 정상일 때만 passed=True를 반환합니다.
    if not all(checks.values()):
        raise AssertionError({"actual": actual, "expected": truth, "checks": checks})
    return {"actual": actual, "checks": checks, "passed": True}
