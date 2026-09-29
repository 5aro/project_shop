# pytest는 함수 이름 test_와 fixture 인자를 보고 테스트와 준비 단계를 연결합니다.

# conftest.py의 fixture는 같은 테스트 폴더에서 import 없이 이름으로 요청할 수 있습니다.
# uuid는 충돌하지 않는 테스트 DB 이름, psycopg.sql은 DB 식별자를 안전하게 조합하는 데 사용합니다.
import os
import uuid
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from shopscope.config import DSN
from shopscope.db import initialize, connect


# 세션 전체에서 고유 이름의 임시 DB를 하나 생성합니다. yield 전은 준비, 이후는 정리이며 CREATE/DROP DATABASE는 autocommit이 필요합니다.
@pytest.fixture(scope="session")
def test_dsn():
    # 새 테스트 DB만 생성/삭제. 기존 shopscope와 학습 DB를 초기화하지 않습니다.

    # 접속 문자열을 딕셔너리로 풀어 호스트/사용자 설정은 유지하고 dbname만 바꿉니다.
    # SHOPSCOPE_TEST_ADMIN_DSN이 없으면 기본 DSN을 사용하므로 해당 계정에 DB 생성 권한이 필요합니다.
    config = conninfo_to_dict(os.environ.get("SHOPSCOPE_TEST_ADMIN_DSN", DSN))

    # 실행마다 다른 접미사를 붙여 기존 shopscope DB와 이름이 겹치지 않게 합니다.
    # DROP 대상도 이 변수를 사용하므로 테스트가 만든 DB만 정리합니다.
    name = "shopscope_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(**config, autocommit=True) as conn:

        # 테이블/DB 이름 같은 식별자는 %s 값 바인딩을 쓸 수 없습니다.
        # sql.Identifier가 인용 규칙에 맞게 이름을 감싸고 SQL.format으로 안전하게 합칩니다.
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    test_config = dict(config, dbname=name)
    dsn = make_conninfo(**test_config)
    initialize(dsn)

    # 이 지점에서 테스트에 접속 문자열을 제공합니다.
    # 세션 fixture를 사용하는 테스트들이 끝나면 아래 코드로 돌아와 임시 DB를 삭제합니다.
    yield dsn
    with psycopg.connect(**config, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )


# 각 테스트 전에 임시 DB의 테이블만 비웁니다. 앞 테스트 데이터가 다음 테스트의 결과를 바꾸지 않도록 격리합니다.
@pytest.fixture
def db(test_dsn):

    # 각 테스트가 공통 세션 DB를 쓰더라도 시작 전에 행을 비워 독립성을 유지합니다.
    # CASCADE는 외래키로 연결된 테이블도 함께 처리합니다. 연결 대상은 위에서 만든 임시 DB입니다.
    with connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE products,orders,payments,shipments,raw_events,quarantine,processed_batches,pipeline_runs,alerts,mart_daily,click_events CASCADE"
        )
    return test_dsn
