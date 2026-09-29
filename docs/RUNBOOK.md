# 실행과 운영

## 설치

README의 명령을 프로젝트 루트에서 실행합니다. Python 가상환경은 `.venv`, PostgreSQL은 `127.0.0.1:55432`를 사용합니다. `SHOPSCOPE_DSN`, `SHOPSCOPE_ROOT`, `SHOPSCOPE_SOURCE`로 설정을 바꿀 수 있습니다. 테스트는 임의 이름의 새 DB를 만들고 그 DB만 제거하므로 DB 생성 권한이 필요합니다. 운영 DB 계정으로 테스트하지 마세요.

## 확장 실험

```sh
source .venv/bin/activate
python -m shopscope benchmark --rows 200000 --repeats 3
python -m shopscope profile
python -m shopscope sql-lab
python -m shopscope search-lab
docker compose --profile labs up -d redis neo4j mongo
python -m shopscope storage-lab
pip install -e '.[semantic]'
python -m shopscope semantic-lab
python -m shopscope report
```

Neo4j 초기 기동에는 시간이 필요합니다. MongoDB 연결 실패는 storage.json에 blocked로 기록하며 Redis·Neo4j 검증은 계속합니다. 최초 의미 검색 실행은 Hugging Face 모델을 다운로드합니다. 결과를 갱신한 뒤 대시보드를 새로고침하세요. benchmark는 파일 생성 비용을 제외한 집계 시간과 프로세스 전체 시간을 구분하며, peak RSS에는 라이브러리 메모리도 포함됩니다.

현재 검증 머신의 Docker Linux 커널과 MongoDB 8.0.32가 호환되지 않아 MongoDB는 기동하지 못했습니다. [공식 호환성 공지](https://www.mongodb.com/docs/manual/release-notes/8.0/)를 확인해 지원 커널의 Docker 환경에서 재실행해야 합니다. 시작 보호 장치를 우회하지 않습니다.

## Airflow

```sh
docker compose --profile airflow up -d --build
# 최초 초기화 완료 후 실행
docker compose exec airflow airflow dags list-import-errors
docker compose exec airflow airflow dags test commerce_daily 2026-08-01
```

UI는 http://127.0.0.1:58080 입니다. 로컬 standalone에서 생성된 비밀번호는 컨테이너의 Airflow 안내를 확인하세요. 저장소에 비밀번호 파일을 커밋하지 않습니다. DAG는 기본 paused입니다. 매일 02:00 KST, catchup=False, 동시 실행 1개, 재시도 2회로 구성했습니다. fixture의 수신 날짜는 2026-08-01~09-03이며 현재 날짜로 무작정 실행하면 원천이 없어 실패합니다. UI 수동 실행 시 `business_date` 파라미터에 위 날짜를 지정합니다. 장기 스케줄 운영은 별도 검증 대상입니다.

## 장애 대응

1. 연결 실패: Docker 상태와 프로젝트 전용 포트를 확인합니다.
2. manifest 오류: 해당 날짜를 다시 collect하고 load합니다. raw 파일을 임의로 수정해 성공 처리하지 않습니다.
3. 품질 게이트 실패: pipeline_runs·alerts·quarantine과 원천을 조사합니다. 실패 배치의 변경은 롤백됩니다.
4. 늦은 이벤트: 수신 날짜를 collect/load하면 과거 주문 코호트가 갱신됩니다.
5. 재실행: 동일 manifest는 skip됩니다. 내용이 다른 동일 이벤트 ID는 자동 덮어쓰기하지 않습니다.

개별 수집은 다른 터미널에서 `python -m shopscope source`를 실행한 뒤 `python -m shopscope collect 2026-08-01`, `python -m shopscope load 2026-08-01` 순서로 실행합니다. 전체 데모는 서버를 내부적으로 시작하므로 별도 source가 필요 없습니다.

## 종료와 데이터 보존

```sh
docker compose --profile labs --profile airflow stop
```

볼륨은 유지됩니다. 대시보드 터미널은 Ctrl+C로 종료합니다. `down -v`는 저장 데이터를 삭제하므로 일반 종료에 사용하지 않습니다. 기존 KG 컨테이너에는 이 명령이 적용되지 않습니다.

백업 예시: `docker compose exec -T postgres pg_dump -U shopscope -d shopscope -Fc > data/shopscope.dump`. 복구는 새 DB에서 먼저 검증하세요. 백업·복구 실습 명령은 제공하지만 이번 완료 검증에는 포함하지 않았습니다.

## Selenium 선택 실습

`pip install -e '.[selenium]'` 후 로컬 source를 실행합니다. 별도 Python에서 `from shopscope.collect import selenium_catalog; print(selenium_catalog())`로 동적 상품 ID를 수집합니다. Chrome과 드라이버 환경이 필요하며 최초 드라이버 설치에 인터넷이 필요할 수 있습니다. 이 경로는 이번 검증에서 실행하지 않았습니다.
