"""6. 데이터베이스 관리
수집·정제된 데이터를 저장하기 위한 데이터베이스와 테이블 구조를 관리합니다."""


# @contextmanager는 yield가 있는 함수를 with 구문에서 쓸 수 있게 바꿉니다.
# dict_row는 조회 결과를 튜플 대신 열 이름으로 접근하는 딕셔너리로 만듭니다.
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from .config import DSN, ROOT


# DB 연결을 with 문에 빌려주는 컨텍스트 관리자입니다. yield 이후에는 psycopg가 커밋/롤백과 연결 종료를 담당합니다.
@contextmanager
def connect(dsn=None):
    # with 블록 성공 → commit, 예외 → rollback. 금액과 체크포인트가 함께 확정됩니다.

    # dsn 인자가 없으면 공통 DSN을 사용합니다. connect_timeout은 연결 대기 제한입니다.
    # conn은 SQL을 실행하는 연결 객체이고, conn.execute(...).fetchone()은 결과 한 행을 읽습니다.
    with psycopg.connect(dsn or DSN, row_factory=dict_row, connect_timeout=10) as conn:

        # DB 연결의 기본 시간대를 한국 시각으로 맞춥니다.
        # timestamptz의 표현과 날짜 변환에 영향을 주며 원본 문자열을 단순히 바꾸는 작업은 아닙니다.
        conn.execute("SET TIME ZONE 'Asia/Seoul'")

        # INSERT INTO orders처럼 스키마를 생략하면 shop을 먼저 찾게 합니다.
        # public에는 pgvector 같은 확장이 설치되어 있을 수 있어 탐색 경로에 포함합니다.
        conn.execute("SET search_path TO shop, public")

        # 여기서 호출자의 with 블록으로 연결을 넘깁니다.
        # 호출자 블록이 끝나면 여기로 돌아와 psycopg의 with가 정상 시 commit, 예외 시 rollback합니다.
        yield conn


# 테이블 정의 → 뷰 정의 순서로 실행합니다. IF NOT EXISTS는 기존 테이블 구조의 변경까지 적용하는 마이그레이션은 아닙니다.
def initialize(dsn=None):
    with connect(dsn) as conn:

        # 먼저 테이블/인덱스를 만든 뒤 그 테이블을 참조하는 뷰를 만듭니다.
        # 두 SQL 파일 모두 같은 연결의 트랜잭션에서 실행되어 초기화 도중 실패하면 함께 취소됩니다.
        conn.execute((ROOT / "sql/001_schema.sql").read_text())
        conn.execute((ROOT / "sql/002_marts.sql").read_text())
