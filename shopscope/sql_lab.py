"""9. SQL 분석 실습
데이터베이스에 적재된 데이터를 SQL로 분석하는 실습 모듈입니다."""

from .db import connect
from .generate import write_json
from .config import REPORTS, ROOT


# 임시 테이블에서 인덱스 전후 실행 계획과 JSONB 속성 추출, CRUD 롤백을 관찰한 뒤 분석 SQL을 실행합니다.
def run():

    # 이 연결 안에서 임시 테이블을 만들고 모든 SQL 실험을 수행합니다.
    # 임시 테이블은 세션 밖에서 공유되지 않으므로 실제 주문 테이블을 실험 데이터로 덮어쓰지 않습니다.
    with connect() as conn:
        conn.execute(
            """CREATE TEMP TABLE index_lab AS SELECT i id, 'seller-'||(i%1000) seller,
          jsonb_build_object('seller','seller-'||(i%1000),'color',CASE WHEN i%1000=0 THEN 'rare' ELSE 'common' END,'pages',i%20) metadata
          FROM generate_series(1,100000) i"""
        )
        # ANALYZE는 테이블 통계를 갱신합니다. EXPLAIN ANALYZE는 이름과 달리 실제 쿼리를 실행해 시간을 측정합니다.
        conn.execute("ANALYZE index_lab")

        # 동일 쿼리의 before/after 계획을 저장할 딕셔너리입니다.
        # B-tree는 일반 열 동등 조건, GIN은 JSON 포함 조건, 표현식 인덱스는 JSON에서 꺼낸 텍스트 조건과 대응합니다.
        cases = {}
        queries = {
            "btree": "SELECT * FROM index_lab WHERE seller='seller-0'",
            "gin": 'SELECT * FROM index_lab WHERE metadata @> \'{"color":"rare"}\'',
            "expression": "SELECT * FROM index_lab WHERE metadata->>'color'='rare'",
        }

        # 인덱스를 만들기 전 같은 쿼리들을 실행하여 비교 기준을 남깁니다.
        # EXPLAIN ANALYZE는 실제 실행하고 BUFFERS는 읽기/캐시 사용 정보를 추가합니다.
        # FORMAT JSON은 결과를 기계가 읽기 쉬운 구조로 받기 위한 옵션입니다.
        for name, q in queries.items():
            cases[name] = {
                "before": conn.execute(
                    "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + q
                ).fetchone()
            }

        # seller 열에 B-tree를 만듭니다. 이어 JSON 포함 연산용 GIN과 color 추출 표현식 인덱스를 만듭니다.
        # 인덱스가 늘면 읽기에 도움을 줄 수 있지만 저장 공간과 쓰기 갱신 비용도 늘어납니다.
        conn.execute("CREATE INDEX lab_btree ON index_lab(seller)")
        conn.execute(
            "CREATE INDEX lab_gin ON index_lab USING gin(metadata jsonb_path_ops)"
        )
        conn.execute("CREATE INDEX lab_expression ON index_lab((metadata->>'color'))")
        conn.execute("ANALYZE index_lab")

        # 같은 쿼리를 다시 실행해 after 계획에 넣습니다.
        # 플래너가 어떤 인덱스를 선택했는지와 실제 시간을 비교합니다.
        # 앞선 읽기로 캐시가 따뜻해졌을 수 있어 전후 시간 차이를 전부 인덱스 효과로 단정하지 않습니다.
        for name, q in queries.items():
            cases[name]["after"] = conn.execute(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + q
            ).fetchone()
        # 자주 조회하는 JSONB 속성을 일반 열로 승격하는 예입니다. 이후 두 표현이 같은지 NULL까지 비교합니다.
        conn.execute("ALTER TABLE index_lab ADD COLUMN color text")
        conn.execute("UPDATE index_lab SET color=metadata->>'color'")

        # IS DISTINCT FROM은 NULL도 값처럼 비교하는 동등성 검사입니다.
        # 일반 <>는 NULL 비교 결과가 unknown이므로 누락을 제대로 세지 못할 수 있습니다.
        mismatch = conn.execute(
            "SELECT COUNT(*) n FROM index_lab WHERE color IS DISTINCT FROM metadata->>'color'"
        ).fetchone()["n"]
        assert mismatch == 0
        # CRUD는 임시 테이블에서만, SAVEPOINT로 되돌리기까지 확인합니다.

        # SAVEPOINT는 트랜잭션 내부 복구 지점입니다.
        # 이후 INSERT/UPDATE/DELETE를 연습하고 ROLLBACK TO로 해당 지점까지 되돌립니다.
        # 바깥 트랜잭션 전체를 종료하는 ROLLBACK과 다릅니다.
        conn.execute("SAVEPOINT crud")
        conn.execute(
            "INSERT INTO index_lab(id,seller,metadata,color) VALUES (100001,'demo','{}','blue')"
        )
        conn.execute(
            "UPDATE index_lab SET metadata=jsonb_set(metadata,'{note}','\"updated\"') WHERE id=100001"
        )
        assert (
            conn.execute("SELECT metadata FROM index_lab WHERE id=100001").fetchone()[
                "metadata"
            ]["note"]
            == "updated"
        )
        conn.execute("DELETE FROM index_lab WHERE id=100001")
        conn.execute("ROLLBACK TO SAVEPOINT crud")
        total = conn.execute("SELECT COUNT(*) n FROM index_lab").fetchone()["n"]
        assert total == 100000
        # 실 데이터 분석 SQL 파일도 실제로 실행합니다.

        # 파일에 작성된 분석 예제들을 실행해 문법과 테이블 참조를 확인합니다.
        # 이 코드는 각 SELECT 결과를 전부 보고서에 수집하지는 않습니다. 결과를 학습하려면 SQL 도구에서 개별 조회하세요.
        conn.execute((ROOT / "sql/003_analysis.sql").read_text())

    # 계획 원문은 reports/sql.json에 저장하고 CLI에는 간단한 요약만 반환합니다.
    # 함수가 반환하는 값과 파일로 남기는 상세 근거가 서로 다름을 확인하세요.
    result = {
        "rows": 100000,
        "plans": cases,
        "promotion_mismatch": mismatch,
        "crud_rollback_passed": True,
        "analysis_sql_executed": True,
        "note": "플래너 기본 설정을 유지한 실측. 한 번의 warm-cache 계획이며 일반적인 배율 보장 아님.",
    }
    write_json(REPORTS / "sql.json", result)
    return {
        "rows": 100000,
        "promotion_mismatch": mismatch,
        "crud_rollback_passed": True,
    }
