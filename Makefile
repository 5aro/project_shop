# ?=는 호출자가 PYTHON을 지정하지 않은 경우에만 기본값을 줍니다. 각 명령 줄의 시작은 공백이 아니라 탭입니다.
PYTHON ?= .venv/bin/python
# 이 이름들은 파일 생성 규칙이 아니라 실행 명령입니다. 같은 이름의 파일이 있어도 실행하게 합니다.
.PHONY: setup db demo test report dashboard labs

# make setup은 의존성 설치 스크립트를 실행합니다.
# setup/db/demo는 자동으로 서로 의존하도록 선언된 타깃이 아니므로 처음에는 README 순서를 따라 실행합니다.
setup:
	bash scripts/bootstrap.sh
# 기본 DB만 시작하고 준비될 때까지 기다립니다. 나머지 실험 저장소는 compose 프로필로 별도 실행합니다.
db:
	docker compose up -d --wait postgres

# $(PYTHON)은 맨 위 변수값으로 치환됩니다.
# 예: make PYTHON=python3 demo처럼 사용할 실행기를 바꿀 수 있습니다.
demo:
	$(PYTHON) -m shopscope demo

# 새 테스트를 만드는 명령이 아니라 기존 pytest 테스트를 실행합니다.
# 전체 테스트에는 로컬 PostgreSQL이 필요하고 테스트 전용 DB를 생성/삭제합니다.
test:
	$(PYTHON) -m pytest -q

# 현재 DB와 실험 결과에서 대시보드 스냅샷을 다시 만듭니다.
# 대시보드를 여는 것만으로 report가 자동 실행되지는 않습니다.
report:
	$(PYTHON) -m shopscope report
dashboard:
	$(PYTHON) -m shopscope dashboard
# 이 타깃은 네 가지 분석 실험만 실행합니다. storage-lab/semantic-lab은 별도 명령이며 앞서 demo 데이터가 필요합니다.
labs:
	$(PYTHON) -m shopscope benchmark
	$(PYTHON) -m shopscope profile
	$(PYTHON) -m shopscope sql-lab
	$(PYTHON) -m shopscope search-lab
