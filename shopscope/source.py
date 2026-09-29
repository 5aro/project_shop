"""3. 원천 API 서버
생성된 데이터를 실제 외부 API처럼 제공하는 로컬 HTTP 서버입니다.

학습 포인트
- HTTP 서버가 요청(Request)을 받고 응답(Response)을 보내는 과정
- URL path와 query parameter 처리
- JSON / HTML 응답
- 페이지네이션(offset, limit)
- HTTP 상태 코드(200, 400, 404, 503)
- 일시적 장애와 Retry 테스트
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import date
from pathlib import Path
from .config import DATA
from .generate import encode


# 요청을 처리할 Handler 클래스를 만들어 반환하는 함수
# root   : 원천 데이터가 저장된 기본 경로
# faults : True이면 의도적으로 일시적인 503 장애를 발생시킴
def handler_factory(root=DATA, faults=False):

    # 실제 API가 제공할 원천 데이터 위치
    # 예: data/source/
    source_root = Path(root) / "source"

    # 이미 장애를 발생시킨 요청을 기록
    # 같은 요청에 계속 503을 발생시키지 않고 첫 요청에서만 실패시키기 위함

    # 이 집합은 handler_factory 호출 한 번에 만들어지고 반환된 Handler들이 공유합니다.
    # 서버 재시작 시 다시 비워집니다. 영구 장애 이력이 아니라 재시도 실습을 위한 메모리 상태입니다.
    failed = set()

    # HTTP 요청을 실제로 처리하는 클래스
    class Handler(BaseHTTPRequestHandler):

        # BaseHTTPRequestHandler가 기본적으로 출력하는 요청 로그를 끔
        # 실습 시 불필요한 로그를 줄이기 위한 설정
        def log_message(self, *args):
            pass

        # HTTP 응답을 만들어 클라이언트에게 전송하는 공통 함수
        def respond(self, value, status=200, content_type="application/json"):

            # JSON 응답이면 Python 객체를 JSON 문자열로 변환
            # HTML/Text라면 문자열을 그대로 사용
            # 마지막 encode()는 네트워크 전송을 위해 문자열 → bytes 변환
            payload = (
                encode(value) if content_type == "application/json" else value
            ).encode()

            # HTTP 상태 코드 전송
            # 예: 200 OK / 400 Bad Request / 404 Not Found / 503 Service Unavailable
            self.send_response(status)

            # 응답 데이터의 형식과 문자 인코딩 정보
            self.send_header("Content-Type", content_type + "; charset=utf-8")

            # 응답 데이터 크기를 byte 단위로 전달
            self.send_header("Content-Length", str(len(payload)))

            # 429 또는 503 오류라면
            # 클라이언트에게 잠시 후 다시 요청하라는 정보를 전달
            if status in (429, 503):
                self.send_header("Retry-After", "0.05")

            # HTTP Header 작성 종료
            self.end_headers()

            # 실제 응답 Body를 클라이언트에게 전송
            self.wfile.write(payload)

        # GET 요청을 받았을 때 실행되는 메서드
        def do_GET(self):

            # 요청 URL을 path와 query 등으로 분리
            # 예:
            # /api/orders?date=2026-09-01&offset=0&limit=100
            parsed = urlparse(self.path)

            # Query String을 딕셔너리 형태로 변환
            # 예:
            # {"date": ["2026-09-01"], "offset": ["0"], "limit": ["100"]}
            params = parse_qs(parsed.query)

            # 서버가 정상적으로 실행 중인지 확인하는 Health Check API
            if parsed.path == "/health":
                return self.respond({"status": "ok", "synthetic": True})

            # 웹 크롤러 접근 정책을 제공하는 robots.txt
            if parsed.path == "/robots.txt":
                return self.respond(
                    "User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                )

            # ---------------------------------------------------------
            # HTML 데이터 제공
            #
            # /catalog
            # → 서버에서 완성된 HTML 제공
            # → requests + BeautifulSoup 같은 방식으로 수집 가능
            #
            # /dynamic-catalog
            # → JavaScript 실행 후 상품이 생성됨
            # → Selenium 같은 브라우저 자동화 도구 실습 가능
            # ---------------------------------------------------------
            if parsed.path in ("/catalog", "/dynamic-catalog"):

                # products.json 파일을 읽어서 Python 객체로 변환
                products = json.loads(
                    (source_root / "products.json").read_text()
                )

                # JavaScript로 상품 목록을 나중에 생성하는 동적 페이지
                if parsed.path == "/dynamic-catalog":
                    html = (
                        '<html><meta charset="utf-8"><body>'
                        '<div id="catalog"></div>'
                        '<script>'
                        'setTimeout(()=>{'
                        'document.querySelector("#catalog").innerHTML='
                        + json.dumps(
                            "".join(
                                f'<article data-id="{p["product_id"]}">'
                                f'<h2>{p["title"]}</h2>'
                                f'</article>'
                                for p in products
                            )
                        )
                        + "},100);"
                        "</script></body></html>"
                    )

                # 서버에서 이미 완성된 정적 HTML 페이지
                else:
                    html = (
                        '<html><meta charset="utf-8"><body>'
                        + "".join(
                            f'<article data-id="{p["product_id"]}">'
                            f'<h2>{p["title"]}</h2>'
                            f'<p>{p["description"]}</p>'
                            f'</article>'
                            for p in products
                        )
                        + "</body></html>"
                    )

                # JSON이 아니라 HTML로 응답
                return self.respond(
                    html,
                    content_type="text/html",
                )

            # ---------------------------------------------------------
            # 상품 API
            # GET /api/products
            #
            # HTML 크롤링이 아니라 JSON API를 통한 데이터 수집 실습
            # ---------------------------------------------------------
            if parsed.path == "/api/products":
                return self.respond(
                    json.loads(
                        (source_root / "products.json").read_text()
                    )
                )

            # ---------------------------------------------------------
            # 주문 / 결제 / 배송 API 처리
            #
            # /api/orders    → orders
            # /api/payments  → payments
            # /api/shipments → shipments
            # ---------------------------------------------------------

            # "/api/" 부분을 제거해서 데이터 종류만 추출
            # 예: "/api/orders" → "orders"
            source = parsed.path.removeprefix("/api/")

            # 허용된 API가 아니면 404 반환
            if source not in ("orders", "payments", "shipments"):
                return self.respond(
                    {"error": "not found"},
                    404,
                )

            # ---------------------------------------------------------
            # Query Parameter 검증
            # ---------------------------------------------------------
            try:
                # date가 실제 날짜 형식인지 검사
                # 예: "2026-09-01"
                day = str(date.fromisoformat(params["date"][0]))

                # 페이지 시작 위치
                # 없으면 기본값 0
                offset = int(
                    params.get("offset", ["0"])[0]
                )

                # 한 번에 가져올 데이터 개수
                # 없으면 기본값 100
                limit = int(
                    params.get("limit", ["100"])[0]
                )

                # 잘못된 페이지네이션 값 방지
                if offset < 0 or not 1 <= limit <= 1000:
                    raise ValueError()

            # date가 없거나 숫자 변환/날짜 변환에 실패하면 400
            except (KeyError, ValueError):
                return self.respond(
                    {"error": "invalid parameters"},
                    400,
                )

            # ---------------------------------------------------------
            # 날짜별 Partition 찾기
            #
            # 예:
            # data/source/2026-09-01/orders.jsonl
            # data/source/2026-09-01/payments.jsonl
            # ---------------------------------------------------------
            path = source_root / day / f"{source}.jsonl"

            # 해당 날짜의 데이터가 존재하지 않으면 404
            if not path.exists():
                return self.respond(
                    {"error": "partition not found"},
                    404,
                )

            # ---------------------------------------------------------
            # 의도적인 503 장애 발생
            #
            # 실제 외부 API에서는 네트워크 문제나 서버 과부하 등으로
            # 일시적인 장애가 발생할 수 있음.
            #
            # 이를 재현하여 Retry 로직을 테스트함.
            # ---------------------------------------------------------

            # 어떤 요청에서 장애가 발생했는지 식별하기 위한 키
            fault_key = (source, day, offset)

            # faults=True이고 첫 페이지 요청이며
            # 아직 실패시킨 적 없는 요청이라면 한 번만 503 발생
            if faults and offset == 0 and fault_key not in failed:

                # 이미 실패한 요청으로 기록
                failed.add(fault_key)

                return self.respond(
                    {"error": "injected transient fault"},
                    503,
                )

            # ---------------------------------------------------------
            # JSONL 파일 읽기
            #
            # JSONL:
            # 한 줄에 JSON 객체 하나가 저장되는 형식
            # ---------------------------------------------------------
            rows = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line
            ]

            # ---------------------------------------------------------
            # 페이지네이션
            #
            # 예:
            # offset=100
            # limit=50
            #
            # → rows[100:150]
            # ---------------------------------------------------------
            end = offset + limit

            # 현재 페이지 데이터와 다음 페이지 위치를 반환
            self.respond(
                {
                    # 현재 페이지 데이터
                    "items": rows[offset:end],

                    # 전체 데이터 개수
                    "total": len(rows),

                    # 다음 페이지가 있으면 다음 offset
                    # 없으면 None(JSON에서는 null)
                    "next_offset": (
                        end if end < len(rows) else None
                    ),
                }
            )

    # 완성된 HTTP 요청 처리 클래스를 반환

    # 클래스 인스턴스가 아니라 클래스 자체를 반환합니다.
    # HTTP 서버가 요청을 받을 때 이 클래스로 요청 처리 객체를 만들어 do_GET을 호출합니다.
    return Handler


# ---------------------------------------------------------
# 실제 HTTP 서버 실행
# ---------------------------------------------------------
def serve(
    host="127.0.0.1",
    port=8765,
    root=DATA,
    faults=False,
):

    # ThreadingHTTPServer
    # → 여러 HTTP 요청을 각각의 Thread에서 처리할 수 있는 서버
    server = ThreadingHTTPServer(
        (host, port),
        handler_factory(root, faults),
    )

    # 서버 주소 출력
    print(
        f"Synthetic source: http://{host}:{server.server_port}",
        flush=True,
    )

    # 서버를 종료하기 전까지 계속 요청을 기다림
    server.serve_forever()