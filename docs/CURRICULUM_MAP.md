# 노션 수업 → 구현 대응표

제공된 노션의 20개 하위 수업 주제를 이커머스 사례로 연결했습니다. 원문을 복제하거나 모든 수업의 개별 예제를 그대로 제출한 것은 아닙니다. HDFS·외부 공시 데이터 같은 별도 환경의 예시는 로컬 합성 로그·상품 데이터로 대체합니다. 미실행 항목도 명시했습니다.

| 수업 원문 | 연결한 개념 | 구현 파일 | 검증 근거(reports/) |
|---|---|---|---|
| [NumPy 벡터화·메모리](https://traveling-goat-521.notion.site/Numpy-3d8ff449f16c805c9299e25f673891ed) | 벡터화/루프, view/copy, broadcasting, dtype | shopscope/benchmark.py | benchmark.json |
| [Pandas 대용량](https://traveling-goat-521.notion.site/Pandas-chunksize-dtype-3d8ff449f16c808f8598deeb2b76e40b) | chunksize, category/downcast, 집계 동등성 | shopscope/benchmark.py | benchmark.json |
| [멀티프로세싱·스레딩](https://traveling-goat-521.notion.site/3d1ff449f16c80009c14de3bcef1f5df) | 순차/ThreadPool/ProcessPool 로그 집계; 수집 I/O 병렬화 | shopscope/benchmark.py, shopscope/collect.py | benchmark.json |
| [Polars·Dask](https://traveling-goat-521.notion.site/Polars-Dask-3d1ff449f16c80baa2eaca0aaf0dcc50) | eager/lazy, streaming, Parquet, 실행계획, Dask 집계 | shopscope/benchmark.py | benchmark.json, polars_plan.txt |
| [로그 병렬 분석 과제](https://traveling-goat-521.notion.site/3d1ff449f16c800cb1ebcb986fdcb7a5) | 파일별 병렬 reduce, 가중 분위수, 시간대 오류율·이상치 | shopscope/benchmark.py | benchmark.json |
| [SQL 기초](https://traveling-goat-521.notion.site/SQL_-bf4ff449f16c82a89f1881a224208b10) | SELECT/WHERE/GROUP BY/HAVING/JOIN, CRUD·SAVEPOINT | sql/003_analysis.sql, shopscope/sql_lab.py | sql.json |
| [PostgreSQL 기초·고급](https://traveling-goat-521.notion.site/PostgreSQL-3e1ff449f16c81779107f2e29ed0f3d9) | PK/FK/CHECK, 트랜잭션, EXPLAIN ANALYZE BUFFERS, B-tree | sql/001_schema.sql, shopscope/sql_lab.py | sql.json; 백업 예시는 RUNBOOK, 복구 미실행 |
| [윈도우 함수](https://traveling-goat-521.notion.site/7abff449f16c829aa939815547c288c2) | ROW_NUMBER/RANK/DENSE_RANK, LAG/LEAD, 이동평균, 세션화 | sql/002_marts.sql, sql/003_analysis.sql | sql.json, tests/test_pipeline.py |
| [JSONB와 인덱싱](https://traveling-goat-521.notion.site/JSONB-84aff449f16c83b88f3201983d79a6e6) | 가변 속성, GIN/표현식 인덱스, 컬럼 승격 비교 | shopscope/sql_lab.py | sql.json |
| [pgvector·Neo4j](https://traveling-goat-521.notion.site/pgvector-neo4j-3e2ff449f16c81d685cfea4d981713c8) | cosine, HNSW/IVFFlat recall, 실제 MiniLM, 그래프 2홉 | shopscope/search.py, shopscope/semantic.py, shopscope/storage.py | search.json, semantic.json, storage.json |
| [MongoDB·Redis](https://traveling-goat-521.notion.site/MongoDB-Redis-NoSQL-3e2ff449f16c8187b92dd041d3a7f513) | 문서 upsert·인덱스 비교, cache-aside 개념, TTL·무효화 | shopscope/storage.py | storage.json: Redis 검증, MongoDB blocked |
| [하이브리드 DB 과제](https://traveling-goat-521.notion.site/DB-3e7ff449f16c818a92daf63e640464a0) | 관계형 조건·JSONB·전문검색·벡터 검색, 모델 버전 | shopscope/search.py, shopscope/semantic.py | search.json, semantic.json |
| [ETL/ELT 아키텍처](https://traveling-goat-521.notion.site/ETL-ELT-3e8ff449f16c8198895cefdd5fbca5db) | raw→validated→mart, 증분 원장·전체 마트, 멱등성 | shopscope/pipeline.py, docs/ARCHITECTURE.md | demo_evidence.json |
| [Airflow](https://traveling-goat-521.notion.site/Airflow-DAG-Operator-Scheduler-3e8ff449f16c81239e5fc8438d2e1900) | TaskFlow DAG, 의존성, retries, schedule, callback | dags/commerce_daily.py | airflow_test.log: DAG test 성공; 정기 운영 미검증 |
| [크롤링·API](https://traveling-goat-521.notion.site/BeautifulSoup-Selenium-API-3e8ff449f16c8185be5ae77a4240384e) | 페이지네이션, 503 재시도, BS4, Selenium 명시적 대기 | shopscope/collect.py, shopscope/source.py | demo_evidence.json; Selenium 구현만, 브라우저 미실행 |
| [자동 적재·모니터링](https://traveling-goat-521.notion.site/3e8ff449f16c81169b59cc18aad4183d) | 실행 기록, 품질 게이트, 경고, 재실행·복구 | shopscope/pipeline.py, dags/commerce_daily.py | tests.log, dashboard.json |
| [파이프라인 종합 과제](https://traveling-goat-521.notion.site/3e8ff449f16c811d8d11cea712221847) | 수집부터 시각화까지 재현, 실패 주입·정답 검증 | shopscope/cli.py, tests/test_pipeline.py | demo_evidence.json, tests.log |
| [정형 통계·비정형 프로파일](https://traveling-goat-521.notion.site/3e8ff449f16c81f2b857e5f388589aad) | 왜도/정규성/IQR/z/log-z/IsolationForest, 인코딩·중복·이미지 메타데이터 | shopscope/profile.py | profile.json |
| [품질·엔티티 해상도](https://traveling-goat-521.notion.site/3e8ff449f16c81ce835fcd2db4414538) | 6가지 품질, MCAR/MAR/MNAR 주입, 정규화·pair precision/recall | shopscope/profile.py, shopscope/quality.py | profile.json |
| [EDA 보고서 과제](https://traveling-goat-521.notion.site/EDA-3e8ff449f16c81afacd2c3e1a57ab019) | 출처·프로파일·품질·문서처리 권고·아키텍처 5항목 | shopscope/report.py | report.md |
