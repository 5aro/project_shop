#!/usr/bin/env bash
# -e: 명령 실패 시 종료, -u: 미정의 변수 사용 오류, pipefail: 파이프 중간의 실패도 실패로 처리합니다.
set -euo pipefail
# 실행한 위치가 달라도 스크립트 기준 프로젝트 루트로 이동합니다.
cd "$(dirname "$0")/.."
# 가상환경으로 프로젝트 의존성을 분리합니다. -e 설치는 소스 수정이 곧 설치된 패키지에 반영되는 개발 모드입니다.

# python3가 가리키는 인터프리터로 .venv 가상환경을 만듭니다.
# 이후에는 .venv/bin/python을 명시해 활성화 여부와 무관하게 같은 환경에 설치합니다.
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip

# -e는 editable 설치입니다. shopscope 소스 수정이 실행에 바로 반영됩니다.
# .[dev,lab]는 현재 프로젝트와 dev/lab 선택 의존성을 함께 설치한다는 뜻입니다.
# 따옴표는 셸이 대괄호를 파일 패턴으로 해석하지 않도록 합니다.
.venv/bin/python -m pip install -e '.[dev,lab]'
echo '설치 완료. source .venv/bin/activate 후 README 순서로 실행하세요.'
