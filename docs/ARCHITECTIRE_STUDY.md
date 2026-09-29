## 📚 코드 학습 순서

ShopScope 프로젝트는 데이터가 생성되고 수집되어 최종적으로 분석·제공되는 흐름을 따라 학습합니다.

전체 흐름:

`Source → Extract → Validate → Transform/Load → Analyze → Serve`

### 0. `cli.py` — CLI 실행 진입점

프로젝트의 여러 기능을 명령어로 실행할 수 있도록 연결하는 파일입니다.

주요 역할:
- `argparse`를 이용한 CLI 명령어 처리
- `generate`, `collect`, `load`, `report` 등의 명령 분기
- 필요한 모듈만 불러오는 Lazy Import
- 전체 데모 파이프라인 실행
- 파이프라인 재실행을 통한 멱등성 검증
- 로컬 Source API 서버의 Thread 실행 및 종료 관리

핵심 학습 개념:
`CLI`, `argparse`, `Lazy Import`, `Thread`, `try-finally`, `Idempotency`

---

### 1. `config.py` — 프로젝트 공통 설정

프로젝트 여러 모듈에서 공통으로 사용하는 경로와 환경설정을 관리합니다.

주요 역할:
- 데이터 저장 경로 정의
- 리포트 저장 경로 정의
- 프로젝트 공통 설정 관리
- 다른 모듈에서 사용할 설정값 중앙화

핵심 학습 개념:
`Configuration`, `Path`, `환경설정`, `공통 설정 관리`

---

### 2. `generate.py` — 테스트 원천 데이터 생성

실제 외부 서비스 대신 데이터 파이프라인을 실습할 수 있도록 가상의 커머스 데이터를 생성합니다.

주요 역할:
- 테스트용 데이터 생성
- 날짜별 데이터 생성
- JSON 등의 형태로 데이터 저장
- 파이프라인 검증을 위한 Ground Truth 생성

핵심 학습 개념:
`Test Data`, `JSON`, `Serialization`, `Ground Truth`

---

### 3. `source.py` — 원천 API 서버

생성된 데이터를 실제 외부 API처럼 제공하는 로컬 HTTP 서버입니다.

주요 역할:
- HTTP GET 요청 처리
- Query Parameter 처리
- Pagination 구현
- HTTP 응답 생성
- 429 / 503 등의 일시적 장애 재현
- 실제 외부 데이터 Source 환경 모방

핵심 학습 개념:
`HTTP`, `REST API`, `Pagination`, `Status Code`, `429`, `503`

---

### 4. `collect.py` — 데이터 수집

Source API에서 데이터를 가져와 Raw Data 형태로 수집하는 Extract 단계입니다.

주요 역할:
- HTTP API 호출
- 날짜별 데이터 수집
- Pagination 처리
- 일시적인 API 장애에 대한 Retry
- 수집한 원본 데이터 저장
- Catalog 등의 기준정보 수집

핵심 학습 개념:
`Extract`, `API Client`, `Pagination`, `Retry`, `Raw Data`

---

### 5. `quality.py` — 데이터 품질 검증

수집된 데이터가 파이프라인에 적재하기 적합한지 검사합니다.

주요 역할:
- 필수값 검증
- 데이터 타입 및 값 검증
- 비정상 데이터 탐지
- 데이터 품질 규칙 관리
- 잘못된 데이터가 이후 단계로 전달되는 것을 방지

핵심 학습 개념:
`Data Quality`, `Validation`, `Data Integrity`, `Quality Rule`

---

### 6. `db.py` — 데이터베이스 관리

수집·정제된 데이터를 저장하기 위한 데이터베이스와 테이블 구조를 관리합니다.

주요 역할:
- 데이터베이스 연결
- 테이블 및 Schema 생성
- Primary Key / Unique Constraint 관리
- SQL 실행
- Transaction 관리
- Commit / Rollback 처리

핵심 학습 개념:
`Database`, `Schema`, `Primary Key`, `Constraint`, `Transaction`, `Commit`, `Rollback`

---

### 7. `pipeline.py` — 데이터 파이프라인 핵심

수집된 데이터를 검증하고 데이터베이스에 적재하는 프로젝트의 핵심 파이프라인입니다.

주요 역할:
- Catalog 데이터 적재
- 날짜별 데이터 적재
- Click 데이터 적재
- 데이터 변환 및 정제
- Transaction 기반 적재
- 중복 데이터 방지
- 재실행 가능한 파이프라인 구현
- Ground Truth와 실제 적재 결과 비교

핵심 학습 개념:
`ETL`, `ELT`, `Pipeline`, `Transaction`, `Upsert`, `Idempotency`, `Data Consistency`

---

### 8. `commerce_daily.py` — Airflow 파이프라인 오케스트레이션

Apache Airflow를 이용해 일일 커머스 데이터 파이프라인을 자동 실행하고 관리합니다.

주요 역할:
- 매일 오전 2시 DAG 실행
- 데이터 수집 → 적재 → 검증 → 리포트 생성 순서 관리
- 실패한 Task의 Retry 및 오류 기록
- 수동 실행을 위한 `business_date` 지원
- XCom을 통한 작은 Manifest 전달

핵심 학습 개념:
`Airflow`, `DAG`, `TaskFlow`, `Scheduling`, `Orchestration`, `Retry`, `XCom`

---

### 9. `sql_lab.py` — SQL 분석 실습

데이터베이스에 적재된 데이터를 SQL로 분석하는 실습 모듈입니다.

주요 역할:
- SQL Query 실행
- 데이터 집계
- 조건별 분석
- 분석용 데이터 조회
- OLAP 형태의 분석 실습

핵심 학습 개념:
`SQL`, `GROUP BY`, `JOIN`, `Aggregation`, `OLAP`

---

### 10. `storage.py` — 데이터 저장 방식 실습

동일한 데이터를 여러 저장 방식과 파일 포맷으로 다루면서 차이를 학습합니다.

주요 역할:
- 데이터 파일 저장
- 저장 포맷 비교
- 파일 크기 및 구조 비교
- 분석에 적합한 저장 방식 실습

핵심 학습 개념:
`CSV`, `JSON`, `Parquet`, `Columnar Storage`, `Data Lake`

---

### 11. `benchmark.py` — 성능 측정

데이터 처리 방식에 따른 실행시간과 성능 차이를 측정합니다.

주요 역할:
- 대량 데이터 생성
- 반복 실행
- 처리 시간 측정
- 서로 다른 처리 방식의 성능 비교

핵심 학습 개념:
`Benchmark`, `Performance`, `Execution Time`, `Scalability`

---

### 12. `profile.py` — 데이터 프로파일링

데이터의 구조와 분포를 자동으로 조사하여 데이터의 특성을 파악합니다.

주요 역할:
- 데이터 개수 확인
- Null 값 확인
- 중복값 확인
- 값의 분포 조사
- 데이터 특성 파악

핵심 학습 개념:
`Data Profiling`, `Null`, `Duplicate`, `Distribution`, `Statistics`

---

### 13. `search.py` — 데이터 검색

저장된 데이터를 조건에 따라 검색하고 필요한 정보를 찾는 기능을 실습합니다.

주요 역할:
- 데이터 검색
- 검색 조건 처리
- 검색 결과 반환
- 일반적인 키워드 기반 검색 실습

핵심 학습 개념:
`Search`, `Index`, `Query`, `Filtering`

---

### 14. `semantic.py` — 의미 기반 검색

문자열이 정확히 일치하는 검색을 넘어 데이터의 의미를 이용한 검색 방식을 실습합니다.

주요 역할:
- 텍스트 데이터 처리
- 의미 기반 데이터 비교
- Semantic Search 실습
- 일반 검색과 의미 검색 비교

핵심 학습 개념:
`Semantic Search`, `Embedding`, `Vector`, `Similarity`

---

### 15. `report.py` — 결과 리포트 생성

파이프라인 실행과 데이터 분석 결과를 사람이 확인할 수 있는 리포트로 생성합니다.

주요 역할:
- 파이프라인 실행 결과 정리
- 검증 결과 정리
- 분석 결과 출력
- Markdown 등의 리포트 생성

핵심 학습 개념:
`Reporting`, `Pipeline Evidence`, `Data Verification`

---

### 16. `dashboard.py` — 데이터 제공 및 시각화

처리된 데이터를 최종 사용자가 확인할 수 있도록 대시보드 형태로 제공합니다.

주요 역할:
- 분석 결과 조회
- HTTP 서버 실행
- 데이터 시각화
- 사용자에게 최종 데이터 제공

핵심 학습 개념:
`Dashboard`, `Data Serving`, `Visualization`, `HTTP Server`

---

### 17. `__init__.py` / `__main__.py` — Python Package 구조

프로젝트를 Python Package로 구성하고 실행할 수 있도록 만드는 파일입니다.

`__init__.py`
- 디렉터리를 Python Package로 구성
- Package 초기화 코드 정의

`__main__.py`
- `python -m <package>` 형태의 실행 지원
- CLI의 `main()` 함수와 연결

핵심 학습 개념:
`Python Package`, `Module`, `Relative Import`, `python -m`

---

## 🔄 전체 데이터 흐름

generate.py
    ↓
source.py
    ↓ HTTP API
collect.py
    ↓
quality.py
    ↓
pipeline.py
    ↓
commerce_daily.py Airflow
    ↓
db.py
    ↓
sql_lab.py
    ↓
storage / benchmark / profile
    ↓
search / semantic
    ↓
report.py
    ↓
dashboard.py

각 파일을 개별적으로 암기하기보다 데이터가

**생성 → 제공 → 수집 → 검증 → 적재 → 분석 → 제공**

되는 과정에서 각 모듈이 어떤 책임을 담당하는지 이해하는 것을 목표로 합니다.