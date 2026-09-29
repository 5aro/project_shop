"""4. 데이터 수집
Source API에서 데이터를 가져와 Raw Data 형태로 수집하는 Extract 단계입니다."""


# 사용하는 도구
# - hashlib: 저장한 파일 내용의 SHA-256 체크섬 계산
# - json: 응답 JSON → Python 객체 변환
# - time: 재시도 사이 대기
# - ThreadPoolExecutor: 서로 독립적인 원천 요청을 동시에 기다리기
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from .config import DATA, SOURCE
from .generate import encode, write_json, digest


# 수집 대상 이름은 API 경로와 파일 이름에 공통으로 쓰입니다.
# 예: orders → /api/orders → raw/2026-08-01/orders.jsonl
# 튜플로 정의하여 이 모듈이 다루는 원천 종류를 한곳에 모읍니다.
SOURCES = ("orders", "payments", "shipments")


# HTTP 응답을 bytes로 반환합니다. 일시적인 서버/통신 장애만 제한 횟수 재시도하고 나머지 오류는 호출자에게 전달합니다.

# 입력: url=요청 주소, attempts=총 시도 횟수, timeout=한 요청의 제한 시간(초)
# 출력: 응답 본문의 bytes. JSON 해석은 호출한 쪽에서 수행합니다.
# 예: request("http://127.0.0.1:8765/health") → b'{"status":...}'
def request(url, attempts=4, timeout=10):
    # 지수 백오프: 실패가 반복될수록 대기 시간을 늘립니다. Retry-After와 계산한 대기 중 큰 값을 택합니다.
    for attempt in range(attempts):
        try:

            # Request에는 URL과 헤더를, urlopen에는 시간 제한을 전달합니다.
            # User-Agent는 요청 주체를 나타내는 이름입니다.
            # with 블록을 벗어나면 응답 연결 자원이 정리됩니다.
            with urlopen(
                Request(url, headers={"User-Agent": "ShopScope-local-learning/1.0"}),
                timeout=timeout,
            ) as response:
                return response.read()

        # HTTP 응답은 왔지만 상태 코드가 오류인 경우입니다.
        # 429=요청 과다, 500/502/503/504=서버 측 일시 장애 후보입니다.
        # 404처럼 재시도해도 해결되기 어려운 오류는 즉시 다시 raise합니다.
        except HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            try:

                # Retry-After 헤더를 초 단위 숫자로 해석하고 최대 5초로 제한합니다.
                # 헤더가 없으면 0을 사용합니다. 이 구현은 HTTP 날짜 형식은 해석하지 않습니다.
                delay = min(float(e.headers.get("Retry-After", 0)), 5)
            except ValueError:
                delay = 0

        # 연결 실패와 시간 초과는 HTTP 상태 코드 없이도 발생합니다.
        # 마지막 시도이면 원래 예외를 전달하고, 그 전이면 기본 백오프로 재시도합니다.
        except (URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            delay = 0

        # attempt=0,1,2일 때 기본 대기는 0.1, 0.2, 0.4초입니다.
        # 서버가 요청한 대기와 기본 대기 중 긴 것을 선택합니다.
        # 성공 시에는 위 return에서 이미 끝났으므로 여기까지 내려오지 않습니다.
        time.sleep(max(delay, min(0.1 * 2**attempt, 3)))
    raise RuntimeError("unreachable")


# 수신 날짜 하나의 주문·결제·배송을 JSONL로 저장하고, 파일별 건수와 해시를 담은 manifest를 반환합니다.
def collect_day(day, base=SOURCE, root=DATA):

    # 문자열이 실제 달력 날짜인지 먼저 검증합니다.
    # 예: "2026-02-30"이면 파일을 만들기 전에 ValueError가 납니다.
    # 정상 날짜는 다시 YYYY-MM-DD 문자열로 통일합니다.
    day = str(date.fromisoformat(day))

    # Path의 / 연산자는 경로를 이어 붙입니다.
    # root가 data이고 day가 2026-08-01이면 data/raw/2026-08-01입니다.
    destination = Path(root) / "raw" / day

    # parents=True는 중간 폴더도 만들고, exist_ok=True는 이미 있어도 허용합니다.
    # 같은 날짜를 다시 수집하는 경우 폴더가 존재한다는 이유로 실패하지 않습니다.
    destination.mkdir(parents=True, exist_ok=True)

    # 하나의 원천 종류를 끝 페이지까지 수집합니다. 바깥 함수의 day/base/destination을 참조하는 중첩 함수입니다.
    def one(source):

        # 페이지 시작 위치는 첫 행인 0부터입니다.
        # rows에는 여러 페이지의 items를 계속 이어 붙입니다.
        # expected는 서버가 알린 전체 건수, seen은 이미 방문한 offset 집합입니다.
        offset = 0
        rows = []
        # total 값은 페이지 사이의 건수 변화를 감지하지만, 건수가 같은 내용 변경까지 잡아내지는 못합니다.
        expected = None
        seen = set()

        # 페이지 수를 미리 모르므로 종료 조건을 안에서 확인하는 반복문입니다.
        # 다음 위치가 None일 때 break하고, 같은 위치를 다시 만나면 순환 오류로 중단합니다.
        while True:
            if offset in seen:
                raise ValueError("페이지네이션 순환 감지")

            # 요청하기 전에 현재 위치를 방문 기록에 넣습니다.
            # set은 중복 원소를 보관하지 않으며 offset in seen으로 방문 여부를 확인합니다.
            seen.add(offset)

            # 딕셔너리를 URL 질의 문자열로 인코딩합니다.
            # 예: {date: 날짜, offset: 100, limit: 100} → date=...&offset=100&limit=100
            query = urlencode({"date": day, "offset": offset, "limit": 100})

            # f-string으로 원천별 주소를 구성하고 응답 bytes를 JSON 객체로 바꿉니다.
            # page는 items, total, next_offset 키를 가진 딕셔너리여야 합니다.
            page = json.loads(request(f"{base}/api/{source}?{query}"))

            # 첫 페이지에서만 전체 건수를 기억합니다.
            # 이후 페이지의 total이 바뀌면 하나의 일관된 수집 결과로 보지 않고 실패시킵니다.
            if expected is None:
                expected = page["total"]
            if expected != page["total"]:
                raise ValueError("수집 중 원천 스냅샷 변경")

            # extend는 목록의 각 원소를 rows에 추가합니다.
            # append(page["items"])로 쓰면 목록 안에 목록이 생겨 데이터 모양이 달라집니다.
            rows.extend(page["items"])

            # 다음 시작 위치는 클라이언트가 임의 계산하지 않고 서버 응답을 따릅니다.
            # None은 마지막 페이지라는 약속이므로 반복을 종료합니다.
            offset = page["next_offset"]
            if offset is None:
                break
            if not page["items"]:
                raise ValueError("다음 페이지가 있지만 현재 페이지가 비어 있음")

        # 마지막 페이지까지 왔더라도 전체 건수가 맞는지 다시 확인합니다.
        # 페이지 누락 같은 문제를 저장 전에 발견하기 위한 검사입니다.
        if len(rows) != expected:
            raise ValueError("원천 건수와 수집 건수 불일치")
        # JSONL은 한 줄에 객체 하나입니다. 해시는 실제 저장할 bytes 기준으로 계산하므로 파일 변조를 검출할 수 있습니다.

        # 각 행을 일정한 형식의 JSON으로 바꾸고 줄바꿈으로 연결합니다.
        # 마지막 encode()는 문자열을 UTF-8 bytes로 바꾸는 str 메서드입니다.
        # 앞의 encode(r)는 generate.py에서 가져온 JSON 변환 함수로, 이름은 같지만 역할이 다릅니다.
        payload = "".join(encode(r) + "\n" for r in rows).encode()
        path = destination / f"{source}.jsonl"

        # 예: orders.jsonl → orders.tmp에 먼저 저장한 뒤 최종 이름으로 교체합니다.
        # 단일 파일의 부분 저장 노출을 줄이는 방식이며 여러 파일 전체의 동시 교체는 아닙니다.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)

        # 원천 이름과 검사 정보를 한 쌍으로 반환합니다.
        # 예: ("orders", {"rows": 201, "sha256": "..."})
        # 바깥의 dict(...)가 이 쌍들을 원천별 딕셔너리로 묶습니다.
        return source, {
            "rows": len(rows),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    # 이 3개 요청은 독립적인 I/O 작업이므로 스레드로 기다림을 겹칩니다.
    with ThreadPoolExecutor(max_workers=3) as pool:

        # map은 SOURCES의 각 값으로 one을 호출합니다.
        # 최대 세 스레드에서 네트워크 대기를 겹치며 결과를 dict로 모읍니다.
        # 어느 작업이든 예외를 내면 결과를 읽는 이 단계에서 호출자에게 전달됩니다.
        files = dict(pool.map(one, SOURCES))
    if sum(f["rows"] for f in files.values()) == 0:
        raise ValueError("전체 원천 0건: 자동 성공으로 처리하지 않습니다")

    # 매니페스트는 데이터 자체가 아니라 수집 결과의 명세서입니다.
    # business_date=수신 날짜, files=원천별 건수/해시,
    # synthetic=합성 데이터 표시, schema_version=적재기가 이해해야 할 명세 버전입니다.
    manifest = {
        "business_date": day,
        "files": files,
        "synthetic": True,
        "schema_version": 1,
    }
    # 동일 날짜·동일 파일 정보는 같은 배치 ID가 됩니다. 실행할 때마다 달라지는 run_id와 구분하세요.
    manifest["batch_id"] = day + "-" + digest(manifest)[:16]
    # 완료 표시는 모든 파일을 검증한 후 마지막에 기록합니다.
    write_json(destination / "manifest.json", manifest)
    return manifest


# 상품 API와 정적 HTML의 상품 ID 집합을 대조합니다. 이름이나 가격까지 동일함을 검증하는 것은 아닙니다.
def collect_catalog(base=SOURCE, root=DATA):
    products = json.loads(request(base + "/api/products"))
    html = request(base + "/catalog")

    # HTML 문자열을 탐색 가능한 트리로 파싱합니다.
    # article[data-id]는 data-id 속성을 가진 article 요소를 선택하는 CSS 선택자입니다.
    soup = BeautifulSoup(html, "html.parser")
    ids = [tag["data-id"] for tag in soup.select("article[data-id]")]

    # HTML과 API의 ID를 집합으로 바꾸어 순서에 관계없이 비교합니다.
    # 집합은 중복을 제거하므로 동일 ID가 HTML에 여러 번 있는지는 이 검사만으로 잡지 못합니다.
    if set(ids) != {p["product_id"] for p in products}:
        raise ValueError("API와 HTML 상품 목록 불일치")
    write_json(Path(root) / "raw/products.json", products)
    write_json(
        Path(root) / "raw/catalog_audit.json",
        {"api_count": len(products), "html_count": len(ids), "same_ids": True},
    )
    return products


# JavaScript가 생성한 상품 요소를 기다린 뒤 ID를 반환합니다. API 수집과 달리 실제 브라우저 실행이 필요합니다.
def selenium_catalog(base=SOURCE, remote=None):
    """선택 실험. 동적 DOM을 명시적 대기로 수집합니다. 기본 파이프라인은 API 사용."""
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC


    # 브라우저 실행 설정을 만듭니다. --headless=new는 창을 띄우지 않고 실행합니다.
    # remote가 있으면 원격 브라우저 서버를, 없으면 로컬 Chrome을 사용합니다.
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    driver = (
        webdriver.Remote(command_executor=remote, options=options)
        if remote
        else webdriver.Chrome(options=options)
    )
    try:
        driver.get(base + "/dynamic-catalog")

        # 고정 시간만큼 무조건 자는 대신 원하는 DOM 요소가 생길 때까지 기다립니다.
        # 최대 10초 안에 조건이 충족되면 바로 진행하고 아니면 시간 초과 예외가 납니다.
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article[data-id]"))
        )
        return [
            el.get_attribute("data-id")
            for el in driver.find_elements(By.CSS_SELECTOR, "article[data-id]")
        ]
    finally:

        # 성공·오류 모두 finally를 거치므로 브라우저 프로세스가 남지 않게 종료합니다.
        driver.quit()
