"""
1. 프로젝트 공통 설정(configuration)
프로젝트 여러 모듈에서 공통으로 사용하는 경로와 환경설정을 관리합니다.
"""

# os.environ은 프로세스 환경변수에 접근합니다.
# Path는 문자열 조합 대신 파일 경로를 운영체제에 맞게 다루는 객체입니다.
import os
from pathlib import Path

# 공통 경로 설정
# __file__은 현재 config.py 파일 경로입니다.
# resolve()로 절대 경로를 구하고 parents[0]=shopscope 패키지, parents[1]=프로젝트 루트입니다.
# SHOPSCOPE_ROOT를 지정하면 코드 위치와 다른 데이터/설정 루트를 사용할 수 있습니다.
ROOT = Path(os.environ.get("SHOPSCOPE_ROOT", Path(__file__).resolve().parents[1]))

# DATA는 원천·raw·실험 데이터의 기준 폴더입니다.
# REPORTS는 실행 결과를 저장하는 폴더입니다. / 연산은 경로 결합이며 이 줄이 폴더를 생성하지는 않습니다.
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

# DSN은 DB 접속 문자열입니다.
# postgresql://사용자:암호@호스트:포트/DB이름 형태이며, 환경변수가 없으면 로컬 실습 DB를 사용합니다.
DSN = os.environ.get(
    "SHOPSCOPE_DSN", "postgresql://shopscope:shopscope_local@127.0.0.1:55432/shopscope"
)

# 로컬에서는 127.0.0.1, 컨테이너에서는 source 같은 서비스 이름이 쓰일 수 있습니다.
# collect.py는 이 기본 주소 뒤에 /api/orders 같은 경로를 붙입니다.
SOURCE = os.environ.get("SHOPSCOPE_SOURCE", "http://127.0.0.1:8765")
