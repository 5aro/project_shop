"""
18. 파이프라인 시연
전체 파이프라인을 한 번에 시연하는 end-to-end 실행 명령입니다.
"""
from .config import REPORTS

# end-to-end 데이터 파이프라인 테스트
# 생성부터 보고서까지 실행하고 같은 배치를 다시 적재해 멱등성을 검사합니다. 기존 생성 조건과 다르면 generate 단계에서 중단됩니다.
def run():
    import threading
    from http.server import ThreadingHTTPServer
    from .source import handler_factory
    from .generate import generate, write_json
    from .collect import collect_day, collect_catalog
    from .db import initialize
    from .pipeline import load_catalog, load_day, verify_truth, load_clicks
    from .report import build_report

    # 테스트 데이터 생성

    # 기존 데이터가 없으면 생성하고, 같은 조건으로 생성된 데이터가 있으면 truth를 다시 읽습니다.
    # truth["dates"]는 주문일뿐 아니라 뒤늦은 배송/환불 수신일까지 포함합니다.
    truth = generate()
    # DB 초기화

    # 테이블과 뷰가 준비되도록 스키마 SQL을 실행합니다.
    # 기존 데이터를 비우는 초기화가 아니라 IF NOT EXISTS를 이용한 구조 준비입니다.
    initialize()
    # 로컬 API 서버 실행

    # 포트 0을 주면 OS가 빈 포트를 선택합니다.
    # faults=True인 원천 서버가 각 원천의 첫 페이지에 503을 주어 수집 재시도를 검증합니다.
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(faults=True))
    # Server Thread

    # HTTP 서버가 계속 대기하는 동안 메인 스레드는 수집을 진행해야 합니다.
    # 따라서 serve_forever를 별도 스레드에 두고 start로 실제 실행을 시작합니다.
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        # catalog 수집/적재

        # 안쪽 collect_catalog가 상품 목록을 반환한 뒤 바깥 load_catalog가 DB에 저장합니다.
        # 주문이 참조할 상품을 먼저 준비하지 않으면 unknown_product 격리가 발생합니다.
        load_catalog(collect_catalog(base))
        results = []
        # 날짜별 collect -> load

        # 각 수신 날짜를 수집한 뒤 적재합니다.
        # load_day 반환 지표를 results에 모아 첫 실행 근거로 저장합니다.
        for day in truth["dates"]:
            collect_day(day, base)
            results.append(load_day(day))
            print(f'{day}: {results[-1].get("inserted",0)} inserted', flush=True)
        # 멱등성 검증(idempotency)

        # 첫 실행 결과를 검증한 뒤 모든 날짜를 다시 load합니다.
        # 이미 완료된 배치는 skipped여야 하고, 재실행 전후 actual 합계가 같아야 합니다.
        before = verify_truth()
        repeat = [load_day(day) for day in truth["dates"]]
        after = verify_truth()
        if before["actual"] != after["actual"]:
            raise AssertionError("재실행 정합성 실패")
        # click 적재
        load_clicks()

        # 첫 실행과 반복 실행을 모두 보관하여 결과뿐 아니라 재현 근거도 남깁니다.
        # 이 파일은 테스트 코드와 별도로 사람이 시연 결과를 확인하는 자료입니다.
        evidence = {
            "first_pass": results,
            "repeat_pass": repeat,
            "verification": after,
            "idempotent": True,
            "source_transient_503_injected": True,
        }
        write_json(REPORTS / "demo_evidence.json", evidence)
        # report 생성
        build_report()
        return {
            "verification": after,
            "idempotent": True,
            "report": "reports/report.md",
        }
    # 리소스 정리(resouce cleanup)
    finally:

        # shutdown은 serve_forever 루프를 끝내고 server_close는 소켓을 닫습니다.
        # thread.join은 서버 스레드 종료를 기다려 다음 실행에 자원이 남지 않도록 합니다.
        server.shutdown()
        server.server_close()
        thread.join()
