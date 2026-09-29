"""8. Airflow 파이프라인 오케스트레이션
Apache Airflow를 이용해 일일 커머스 데이터 파이프라인을 자동 실행하고 관리합니다."""


# Airflow의 dag/task 데코레이터는 Python 함수를 스케줄 가능한 작업으로 연결합니다.
# pendulum은 시간대가 있는 날짜를 만들고, timedelta는 재시도/실행 제한 시간을 표현합니다.
import logging
from datetime import timedelta
import pendulum
from airflow.sdk import dag, task, get_current_context, Param


# 콜백에는 실패한 실행의 문맥이 전달됩니다. run_id를 알림 키로 써 같은 실행의 반복 알림을 합칩니다.
def failed(context):
    # 외부 메시지를 보내지 않습니다. 실패는 Airflow 로그/로컬 DB에서 확인합니다.
    from shopscope.db import connect


    # DAG 실행 ID를 포함한 알림 키입니다.
    # 같은 실행의 여러 실패가 발생하면 같은 키의 occurrences를 올려 묶어서 기록합니다.
    key = f'airflow:{context["dag_run"].run_id}'
    with connect() as conn:
        conn.execute(
            """INSERT INTO alerts(alert_key,severity,message) VALUES (%s,'CRITICAL',%s)
          ON CONFLICT(alert_key) DO UPDATE SET occurrences=alerts.occurrences+1,last_seen=now()""",
            (key, "Airflow task failed: " + str(context.get("task_instance"))),
        )
    logging.error("ShopScope DAG failed: %s", key)


# DAG는 작업 의존 관계입니다. 매일 02시에 스케줄되며 catchup=False는 과거 미실행 구간의 자동 소급 생성을 막습니다.
# max_active_runs=1은 이 DAG 실행을 하나로 제한합니다. DB의 advisory lock은 별도 실행 경로까지 쓰기 충돌을 막습니다.
@dag(
    dag_id="commerce_daily",

    # cron 필드 순서는 분·시·일·월·요일입니다. 매일 02:00 실행을 의미합니다.
    # start_date의 Asia/Seoul 시간대가 스케줄의 시간 해석 기준이 됩니다.
    schedule="0 2 * * *",
    start_date=pendulum.datetime(2026, 8, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,

    # retries=2는 최초 시도 이후 최대 두 번의 추가 시도입니다.
    # retry_delay는 시도 사이 대기, execution_timeout은 태스크 한 번의 시간 제한입니다.
    # 최종 실패 시 on_failure_callback에 등록한 failed가 실행됩니다.
    default_args={
        "retries": 2,
        "retry_delay": timedelta(seconds=10),
        "execution_timeout": timedelta(minutes=10),
        "on_failure_callback": failed,
    },

    # 수동 실행 시 business_date로 특정 수신 날짜를 지정할 수 있습니다.
    # Param은 null 또는 문자열을 허용합니다. 실제 날짜 유효성은 수집 함수에서도 검사합니다.
    params={
        "business_date": Param(
            None,
            type=["null", "string"],
            description="수동 실행할 수신 날짜 YYYY-MM-DD",
        )
    },
    tags=["portfolio", "commerce", "synthetic"],
)
# 함수 호출은 작업 그래프를 구성합니다. 내부 @task 함수의 실제 처리는 Airflow가 실행 시점에 수행합니다.
def commerce_daily():
    # 상품 참조 데이터를 먼저 준비하고 수신 날짜의 raw를 수집합니다. 수동 날짜가 없으면 실행 구간 시작 날짜를 사용합니다.
    @task
    def extract():
        from shopscope.collect import collect_day, collect_catalog
        from shopscope.pipeline import load_catalog
        from shopscope.db import initialize


        # 현재 실행의 params와 데이터 처리 구간 등을 가져옵니다.
        # 코드 파일을 읽는 시점이 아니라 태스크 실행 시점의 문맥입니다.
        context = get_current_context()

        # 수동 business_date가 있으면 우선합니다.
        # 없으면 data_interval_start를 한국 시간대로 변환한 날짜를 씁니다.
        # 실제로 태스크가 실행된 현재 날짜와 처리 구간 날짜는 다를 수 있습니다.
        day = (
            context["params"].get("business_date")
            or context["data_interval_start"]
            .in_timezone("Asia/Seoul")
            .date()
            .isoformat()
        )
        initialize()
        load_catalog(collect_catalog())

        # return 값은 다음 태스크가 받아 사용할 작은 manifest입니다.
        # 수집한 모든 행을 태스크 메시지로 넘기는 대신 공유 파일에 저장하고 날짜만 연결합니다.
        return collect_day(day)

    # 태스크 사이에는 manifest 같은 작은 메타데이터를 전달합니다. 실제 데이터는 공유 raw 파일에서 읽습니다.
    @task
    def transform_load(manifest):
        from shopscope.pipeline import load_day


        # 앞 태스크의 manifest에서 수신 날짜를 꺼내 적재합니다.
        # Airflow 재시도가 있어도 load_day의 체크포인트와 중복 검사가 같은 배치를 안전하게 처리하도록 합니다.
        return load_day(manifest["business_date"])

    # 적재 후 재무 규칙과 완료 체크포인트를 재확인합니다. 이 단계 실패는 이미 커밋한 적재를 되돌리지는 않습니다.
    @task
    def validate(result):
        from shopscope.db import connect
        from shopscope.pipeline import check_finance

        with connect() as conn:

            # 원장 합계 규칙을 다시 확인한 뒤 날짜별 성공 체크포인트가 있는지 조회합니다.
            # fetchone()이 None이면 완료 기록이 없으므로 예외를 내 다음 보고서 작업을 막습니다.
            check_finance(conn)
            if not conn.execute(
                "SELECT 1 FROM processed_batches WHERE business_date=%s",
                (result["business_date"],),
            ).fetchone():
                raise ValueError("완료 체크포인트 누락")
        return result

    # 앞 단계가 성공한 뒤 스냅샷을 갱신합니다. 매 태스크가 다른 프로세스에서 실행될 수 있어 DB/파일로 결과를 공유합니다.
    @task
    def publish_report(result):
        from shopscope.report import build_report

        build_report()
        return {"business_date": result["business_date"], "status": "published"}

    # 중첩된 호출이 extract → transform_load → validate → publish_report의 의존 순서를 만듭니다.
    publish_report(validate(transform_load(extract())))



# 모듈을 읽을 때 이 호출로 DAG 객체를 만들어 Airflow에 등록합니다.
# 등록과 매일 실제 태스크 실행은 서로 다른 단계입니다.
commerce_daily()
