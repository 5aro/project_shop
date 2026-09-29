"""15. 결과 리포트 생성
파이프라인 실행과 데이터 분석 결과를 사람이 확인할 수 있는 리포트로 생성합니다."""

import json
from datetime import datetime, timezone
from .config import ROOT, REPORTS
from .db import connect
from .generate import write_json, json_value


# 여러 SQL 결과를 하나의 읽기 전용 DB 스냅샷으로 묶습니다. 실험 JSON 파일은 별도로 읽으므로 DB와 같은 트랜잭션 스냅샷은 아닙니다.
def snapshot(dsn=None):
    with connect(dsn) as conn:
        # REPEATABLE READ는 이 트랜잭션의 여러 SELECT가 일관된 시점의 데이터를 보게 합니다. READ ONLY는 실수로 쓰는 것을 막습니다.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

        # order_finance는 주문당 한 행이므로 COUNT(*)가 주문 수입니다.
        # FILTER 조건으로 배송 완료/지연/불일치 주문만 각각 세고 COALESCE로 빈 합계를 0으로 표현합니다.
        totals = conn.execute(
            """SELECT COUNT(*) orders,COALESCE(SUM(expected_krw),0) gmv,
            COALESCE(SUM(captured_krw),0) captured,COALESCE(SUM(refunded_krw),0) refunded,
            COALESCE(SUM(net_collected_krw),0) net,
            COUNT(*) FILTER(WHERE delivered) delivered,COUNT(*) FILTER(WHERE late) late,
            COUNT(*) FILTER(WHERE mismatch) mismatched FROM order_finance"""
        ).fetchone()

        # 날짜와 채널 조합마다 한 행을 만듭니다.
        # 이 결과가 대시보드 기간/채널 필터의 원천이며, 날짜는 수신일이 아니라 한국 시각의 주문일입니다.
        daily = conn.execute(
            """SELECT (ordered_at AT TIME ZONE 'Asia/Seoul')::date AS "day",channel,
            COUNT(*) orders,SUM(expected_krw) gmv,SUM(captured_krw) captured,SUM(refunded_krw) refunded,
            SUM(net_collected_krw) net,COUNT(*) FILTER(WHERE delivered) delivered,
            COUNT(*) FILTER(WHERE late) late FROM order_finance GROUP BY 1,2 ORDER BY 1,2"""
        ).fetchall()

        # 격리를 원천·사유·상태별로 묶습니다.
        # open은 미해결, resolved는 재검사로 해결된 기록이므로 화면에서 따로 표현합니다.
        quality = conn.execute(
            "SELECT source,reason,status,COUNT(*) count FROM quarantine GROUP BY 1,2,3 ORDER BY 4 DESC"
        ).fetchall()

        # 최근 40번 실행만 가져옵니다.
        # 재실행도 하나의 실행 이력이므로 같은 business_date가 여러 번 나오는 것은 정상입니다.
        runs = conn.execute(
            "SELECT business_date,status,started_at,ended_at,metrics FROM pipeline_runs ORDER BY started_at DESC LIMIT 40"
        ).fetchall()

        # 상품에서 시작하는 LEFT JOIN으로 주문이 없는 상품도 남깁니다.
        # COUNT(o.order_id)는 NULL을 세지 않아 그런 상품의 주문 수가 0이 됩니다.
        # 순수납액 내림차순 상위 10개를 스냅샷에 담습니다.
        products = conn.execute(
            """SELECT p.product_id,p.title,p.category,COUNT(o.order_id) orders,
            COALESCE(SUM(o.net_collected_krw),0) net FROM products p
            LEFT JOIN order_finance o USING(product_id) GROUP BY p.product_id ORDER BY net DESC LIMIT 10"""
        ).fetchall()

        # processed_batches는 성공한 배치 목록입니다.
        # 그중 최대 수신 날짜와 배치 수를 가져오며 pipeline_runs의 실행 횟수와 구분합니다.
        batches = conn.execute(
            "SELECT COUNT(*) count,MAX(business_date) last_date FROM processed_batches"
        ).fetchone()

        # 결제 수신 시각 최댓값을 하나의 행으로 구한 뒤 주문 테이블에 결합합니다.
        # 주문 시작과 가장 늦은 결제 수신을 함께 보여 줘 지연 도착 범위를 확인하는 값입니다.
        time_range = conn.execute(
            "SELECT MIN(ordered_at) first_order,MAX(received_at) last_receipt FROM orders CROSS JOIN (SELECT MAX(received_at) received_at FROM payments) p"
        ).fetchone()
        alerts = conn.execute(
            "SELECT severity,message,occurrences FROM alerts ORDER BY last_seen DESC LIMIT 20"
        ).fetchall()

        # 세션 ID는 고객 안에서만 고유합니다.
        # 따라서 customer_id와 session_id의 조합을 DISTINCT로 세어 전체 세션 수를 구합니다.
        sessions = conn.execute(
            "SELECT COUNT(*) n FROM (SELECT DISTINCT customer_id,session_id FROM customer_sessions) s"
        ).fetchone()["n"]

    # 실험 보고서는 모두 필수는 아닙니다.
    # 파일이 존재하는 실험만 읽어 labs에 넣어 아직 실행하지 않은 기능도 화면에서 표시할 수 있게 합니다.
    labs = {}
    for name in [
        "benchmark",
        "profile",
        "search",
        "semantic",
        "storage",
        "sql",
        "validation",
    ]:
        file = REPORTS / f"{name}.json"
        if file.exists():
            labs[name] = json.loads(file.read_text())

    # DB 결과와 별도 파일 결과를 하나의 스냅샷 객체로 반환합니다.
    # generated_at은 UTC 시각이며 브라우저에서 한국 시각으로 표시합니다.
    # synthetic/currency는 소비자가 데이터를 해석할 때 필요한 메타정보입니다.
    return dict(
        generated_at=datetime.now(timezone.utc),
        synthetic=True,
        currency="KRW",
        totals=totals,
        daily=daily,
        quality=quality,
        runs=runs,
        products=products,
        batches=batches,
        time_range=time_range,
        alerts=alerts,
        sessions=sessions,
        labs=labs,
    )


# 동일한 스냅샷을 JSON·브라우저 데이터·Markdown 보고서로 저장합니다. 새 DB 결과를 화면에 반영하려면 다시 실행해야 합니다.
def build_report(dsn=None):

    # 한 번 얻은 data로 모든 결과 파일을 만듭니다.
    # 각 출력마다 DB를 다시 조회하지 않아 JSON과 Markdown의 기준 데이터가 일치합니다.
    data = snapshot(dsn)
    REPORTS.mkdir(exist_ok=True)
    write_json(REPORTS / "dashboard.json", data)
    # Decimal/날짜를 JSON 형식으로 바꾸고 닫는 태그 시작을 이스케이프합니다. data.js는 매번 생성되므로 학습 설명은 생성 코드에 둡니다.
    serialized = json.dumps(data, ensure_ascii=False, default=json_value).replace(
        "</", "<\\/"
    )

    # 브라우저에서 script 태그로 읽을 수 있도록 window.SHOPSCOPE_DATA에 값을 대입하는 JS 파일을 생성합니다.
    # web/data.js에 직접 쓴 주석이나 수동 변경은 다음 report 실행 때 덮어써집니다.
    (ROOT / "web/data.js").write_text("window.SHOPSCOPE_DATA = " + serialized + ";\n")
    t = data["totals"]

    # 해결된 기록을 제외하고 open 상태만 더해 검토가 필요한 건수를 계산합니다.
    # 아래 f-string의 {표현식}에 실제 수치가 삽입됩니다.
    quarantine = sum(r["count"] for r in data["quality"] if r["status"] == "open")

    # 여기부터의 여러 줄 문자열은 Python 주석이 아니라 생성할 Markdown 문서 본문입니다.
    # 코드를 설명하는 주석은 문자열 밖에 두어 실제 보고서 내용에 섞이지 않도록 합니다.
    text = f"""# ShopScope 데이터 진단 보고서

## 데이터 출처·수집 방식·규모

직접 만든 가상 이커머스 원천입니다. seed=42, 주문 {t['orders']:,}건, 상품 80개입니다.
실제 고객 정보와 실거래는 포함하지 않습니다. 로컬 HTTP API에서 수신 날짜별로 페이지를 순회하고,
파일 해시·건수 매니페스트를 검증한 뒤 PostgreSQL에 적재했습니다. 이 결과로 실제 시장 수요를 추정하지 않습니다.
마지막 처리 파티션: {data['batches']['last_date']}. 생성 시각: {data['generated_at'].isoformat()}.

## 정형/반정형/비정형별 프로파일

| 지표 | 값 | 정의 |
|---|---:|---|
| 주문 GMV | {int(t['gmv']):,}원 | 주문 수량 × 주문 당시 단가, 환불 전 |
| 누적 결제액 | {int(t['captured']):,}원 | 중복 제거한 capture 원장 합계 |
| 누적 환불액 | {int(t['refunded']):,}원 | 수신 완료한 refund 원장 합계 |
| 순수납액 | {int(t['net']):,}원 | 결제 − 환불. 회계상 매출·이익과 다름 |
| 배송 완료 주문 | {t['delivered']:,}건 | 배송 완료 이벤트가 있는 주문 |
| 배송 지연 주문 | {t['late']:,}건 | 완료 시각이 약속 시각보다 늦은 주문 |

주문은 한 주문에 한 상품인 단순화 모델입니다. 채널은 web/app/market, 상품별 속성은 JSONB에 둡니다.
상품 설명의 길이·중복·문자 정규화, 상품 이미지 메타데이터, 통계/이상치 탐지는 `profile.json`을 참고합니다.
일별 지표는 **주문일 코호트**입니다. 8월 1일 주문의 늦은 환불도 8월 1일 순수납액을 바꿉니다.
정산 발생일별 현금 흐름은 별도 SQL(`sql/003_analysis.sql`)로 제공합니다.

## 품질 이슈 목록과 심각도

현재 미해결 격리 {quarantine}건. 원천 JSON과 사유를 보존합니다.

| 출처 | 사유 | 상태 | 건수 |
|---|---|---|---:|
"""

    # 품질 집계 한 행마다 Markdown 표의 한 줄을 붙입니다.
    # 이 루프 이후 공통 해설을 이어 붙인 뒤 파일로 저장합니다.
    for r in data["quality"]:
        text += f"| {r['source']} | {r['reason']} | {r['status']} | {r['count']} |\n"
    text += """
잘못된 수량·부모 없는 결제는 행 단위로 격리하고 경고를 남깁니다. 배치 격리율이 5%를 넘거나 환불이
결제보다 많으면 해당 배치 전체를 롤백합니다. 5%는 합성 시나리오용 정책값이며 실무 기준으로 일반화하지 않습니다.
배송 지연은 업무 성과 문제이지 형식 오류가 아니므로 삭제하지 않습니다. 미래 시점의 배송 미완료를 지연으로
단정하지 않습니다. 결제 불일치도 주문 직후에는 정상일 수 있어 자동 삭제하지 않고 검토 지표로 남깁니다.
정확성은 생성기의 독립된 정답 합계로 검증한 범위에 한합니다. 실제 외부 원장과의 정확성은 검증하지 않았습니다.

## 문서 처리 관점의 권고

1. 상품 설명 원문과 정규화한 텍스트를 구분하여 보관합니다. 유사한 이름만으로 다른 상품을 합치지 않습니다.
2. 바이트 해시는 완전 중복, n-gram 유사도는 근사 중복 후보 탐지에 사용합니다. 근사 중복은 자동 삭제하지 않습니다.
3. 동일 판매자 후보는 이름 정규화 이후에도 판매자 ID 같은 식별 근거로 확정합니다.
4. 통계적 이상치만으로 고액 주문을 제거하지 않습니다. 오류 판정과 업무상 특이 주문을 분리합니다.
5. 단어 해시 벡터 실험의 유사도를 신경망 의미 이해 성능으로 해석하지 않습니다.

## 파이프라인 아키텍처 다이어그램

```mermaid
flowchart LR
  A[합성 주문·결제·배송 API] --> B[페이지 수집 / 재시도]
  B --> C[Raw JSONL + manifest]
  C --> D[검증 / 중복 제거]
  D --> E[PostgreSQL 원장]
  D --> Q[격리 + 재검사]
  E --> F[SQL 마트 / 일별 지표]
  F --> G[대시보드 / EDA 보고서]
  H[Airflow 일 단위 실행] --> B
  P[상품 설명] --> V[pgvector 검색 실험]
  P --> N[MongoDB / Redis / Neo4j 비교 실험]
```

실선은 구현된 흐름입니다. 실제 검증 상태는 `validation.json`과 `docs/VERIFICATION.md`에서 별도로 확인합니다.
이 도식은 배포 완료나 운영 환경 검증을 의미하지 않습니다.
"""

    # 완성된 Markdown과 기계가 읽을 요약 JSON을 별도 파일로 저장합니다.
    # 이후 반환 딕셔너리는 CLI가 화면에 출력할 핵심 지표입니다.
    (REPORTS / "report.md").write_text(text)
    write_json(
        REPORTS / "report_summary.json",
        {
            "synthetic": True,
            "orders": t["orders"],
            "quality_open": quarantine,
            "recommendations_count": 5,
            "sections": 5,
            "net_collected_krw": t["net"],
        },
    )
    return {
        "orders": t["orders"],
        "net_collected_krw": t["net"],
        "quarantined": quarantine,
    }
