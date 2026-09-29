# ShopScope — 이커머스 데이터 엔지니어링 학습 프로젝트

주문·결제·배송 이벤트를 수집하고, 중복과 오류를 통제하여 재실행해도 같은 정산 결과를 만드는 로컬 데이터 플랫폼입니다. **신입 데이터 엔지니어 취업 준비용 완성 예제**이며, Python·SQL 초급자가 완성된 코드를 6주 동안 이해하도록 구성했습니다.

## 주석을 따라 학습하기

코드에 한국어 학습 주석을 추가했습니다. 처음에는 `shopscope/cli.py`에서 명령을 찾고, `generate.py → source.py → collect.py → quality.py → pipeline.py → sql/002_marts.sql → report.py → web/app.js` 순으로 한 주문을 추적하세요. 함수 앞 주석은 역할과 반환값을, 내부 주석은 처리 이유와 주의할 경계를 설명합니다.

[학습 주석 안내](docs/LEARNING_COMMENTS.md)에서 파일별 읽기 순서와 확인 질문을 볼 수 있습니다. 기존 [코드 안내](docs/CODE_TOUR.md)와 [6주 계획](docs/STUDY_PLAN.md)을 함께 사용하세요.

## 먼저 보기

- [대시보드](web/index.html): 파일을 브라우저로 열면 저장된 결과를 즉시 볼 수 있습니다. DB 없이 열립니다.
- [실행 결과 보고서](reports/report.md), [검증 범위](docs/VERIFICATION.md)
- [코드 읽기 안내](docs/CODE_TOUR.md), [6주 학습 계획](docs/STUDY_PLAN.md)
- [노션 20개 수업 대응표](docs/CURRICULUM_MAP.md)
- [포트폴리오 설명·면접 준비](docs/PORTFOLIO.md)

## 구현 결과

합성 주문 6,000건, 상품 80개, 수신일 파티션 34개를 처리했습니다. 결제액 203,049,000원에서 환불 7,937,000원을 제외한 **순수납액 195,112,000원**을 생성기의 정답과 대조했습니다. 오류 31건은 원문과 사유를 보존하여 격리합니다. 전체 재실행의 건수·금액 불변, 실패 롤백, 지연 환불 반영, 분할 결제 집계 등을 자동 테스트합니다.

실거래·실제 개인정보를 사용하지 않습니다. 이 금액은 사업 성과나 회계상 매출이 아닙니다. 한 주문에 한 상품이라는 단순화가 있습니다. 분석·벤치마크 수치는 이 데이터와 실행 환경에 한정합니다.

## 빠른 실행 — macOS / Python 3.12 이상 / Docker Desktop

프로젝트 폴더에서 실행합니다. 최초 설치에는 인터넷이 필요합니다.

```sh
bash scripts/bootstrap.sh
source .venv/bin/activate
docker compose up -d --wait postgres
python -m shopscope demo
python -m pytest -q
python -m shopscope dashboard
```

브라우저에서 http://127.0.0.1:8877 에 접속합니다. `demo`는 생성→로컬 API 수집→검증→적재→재실행 검증→보고서 생성까지 수행합니다. 별도 API 키가 필요 없습니다. 기존 KG 학습 자료와 기존 DB 포트를 사용하지 않습니다. 실습용 계정은 compose에 공개되어 있으므로 외부 서비스로 그대로 배포하지 마세요.

핵심: Python / PostgreSQL / SQL / JSONL / Airflow. 확장: NumPy, Pandas, Polars, Dask, JSONB, pgvector, MiniLM, Redis, Neo4j, MongoDB, BeautifulSoup, Selenium.

## 폴더

| 경로 | 역할 |
|---|---|
| shopscope/ | 수집·정제·적재·실험·보고서 Python 코드 |
| sql/ | 스키마, 마트, 분석 SQL |
| dags/ | Airflow 일별 DAG |
| tests/ | 별도 임시 DB에서 실행하는 테스트 |
| web/ | 저장된 결과를 탐색하는 대시보드 |
| reports/ | 실제 실행 결과와 근거 |
| docs/ | 설계, 운영, 학습, 면접 안내 |
| data/ | 재생성 가능한 합성 데이터; Git 제외 |

확장 실험과 Airflow 실행은 [운영 안내](docs/RUNBOOK.md)를 따르세요. MongoDB 실행과 Selenium 브라우저 수집은 검증 완료 범위에 포함되지 않습니다. 스케줄러의 장기간 정기 운영도 검증하지 않았습니다.
