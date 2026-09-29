"""16. 데이터 제공 및 시각화
처리된 데이터를 최종 사용자가 확인할 수 있도록 대시보드 형태로 제공합니다."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# partial은 함수/클래스에 일부 인자를 미리 고정하는 도구입니다.
# 여기서는 요청 핸들러가 항상 ROOT/web 폴더를 제공하도록 directory를 고정합니다.
from functools import partial
from .config import ROOT


# 정적 파일 서버입니다. DB에 직접 질의하지 않으며 report가 만든 data.js 스냅샷을 브라우저에 제공합니다.
def serve(port=8877):
    print(f"ShopScope dashboard: http://127.0.0.1:{port}", flush=True)
    # web 폴더만 제공합니다. .env나 DB 백업은 이 서버로 노출되지 않습니다.

    # 127.0.0.1 바인딩은 이 컴퓨터에서만 접속하게 합니다.
    # 브라우저 요청마다 정적 HTML/CSS/JS 파일을 읽어 응답합니다.
    # serve_forever는 종료할 때까지 요청을 기다리는 반복 루프입니다.
    ThreadingHTTPServer(
        ("127.0.0.1", port),
        partial(SimpleHTTPRequestHandler, directory=str(ROOT / "web")),
    ).serve_forever()
