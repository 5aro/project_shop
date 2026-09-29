"""
0. CLI 실행 진입점
프로젝트의 여러 기능을 명령어로 실행할 수 있도록 연결하는 파일입니다.
"""


# argparse는 명령행 문자열을 검사하고 Python 값으로 변환하는 표준 라이브러리입니다.
# 예: python -m shopscope generate --days 3에서 generate는 명령, --days 3은 옵션입니다.
import argparse
import json
from pathlib import Path
from .config import DATA, REPORTS


# 인자 정의 → 해석 → 기능 호출 → JSON 출력의 진입점입니다. 각 분기에서 필요한 모듈만 가져와 선택 실험의 의존성을 분리합니다.
def main():
    # CLI parser 생성
    parser = argparse.ArgumentParser(description="ShopScope commerce data platform")
    # subparser 생성

    # dest="command"는 선택한 명령을 args.command에 저장합니다.
    # required=True이므로 명령 없이 실행하면 도움말과 함께 사용 오류가 납니다.
    sub = parser.add_subparsers(dest="command", required=True)

    # source는 API 서버 실행 명령입니다.
    # --host/--port로 바인딩 주소를 정하고 --faults를 붙이면 한 번의 일시 장애를 주입합니다.
    # action="store_true"는 옵션이 있으면 True, 없으면 False를 저장합니다.
    p = sub.add_parser("source")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--faults", action="store_true")

    # generate는 원천 데이터 생성 명령입니다.
    # type=int는 숫자 변환, type=Path는 경로 객체 변환을 argparse에 맡깁니다.
    # 여기의 --root는 generate 명령에만 적용되며 모든 명령의 전역 옵션은 아닙니다.
    p = sub.add_parser("generate")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--per-day", type=int, default=200)
    p.add_argument("--root", type=Path, default=DATA)
    sub.add_parser("init")

    # date처럼 --가 없는 인자는 위치 인자입니다.
    # 예: collect 2026-08-01은 args.date에 날짜 문자열을 넣습니다. 실제 달력 검증은 collect_day에서 합니다.
    p = sub.add_parser("collect")
    p.add_argument("date")
    p = sub.add_parser("load")
    p.add_argument("date")

    # 이 옵션은 load의 커밋 직전 장애를 켭니다.
    # 실패 후 재실행해도 원장/마트/체크포인트가 어긋나지 않는지 실습할 때 사용합니다.
    p.add_argument("--inject-failure", action="store_true")
    sub.add_parser("demo")
    sub.add_parser("verify")
    sub.add_parser("report")

    # rows는 비교용 CSV 행 수, repeats는 각 엔진 측정 반복 수입니다.
    # 기본값 200000행/3회는 실제 실행 인자가 없을 때 적용됩니다.
    p = sub.add_parser("benchmark")
    p.add_argument("--rows", type=int, default=200000)
    p.add_argument("--repeats", type=int, default=3)
    sub.add_parser("profile")
    sub.add_parser("search-lab")
    sub.add_parser("semantic-lab")
    sub.add_parser("storage-lab")
    sub.add_parser("sql-lab")
    p = sub.add_parser("dashboard")
    p.add_argument("--port", type=int, default=8877)

    # 실제 명령 읽기

    # 사용자가 준 명령과 옵션을 Namespace 객체로 해석합니다.
    # 이 시점 이후 args.command, args.port처럼 속성으로 입력값을 읽을 수 있습니다.
    args = parser.parse_args()
    result = None

    # 서버는 serve_forever로 계속 대기하므로 return serve(...)로 분기를 끝냅니다.
    # 아래 일반 실행 결과 JSON 출력까지 내려가지 않는 명령입니다.
    if args.command == "source":
        # Lazy import
        from .source import serve

        return serve(args.host, args.port, faults=args.faults)

    # 일회성 작업은 반환값을 result에 담습니다.
    # 각 분기 내부 import는 해당 기능을 선택했을 때만 모듈과 선택 의존성을 불러오는 방식입니다.
    if args.command == "generate":
        from .generate import generate

        result = generate(args.days, args.per_day, root=args.root)
    elif args.command == "init":
        from .db import initialize

        initialize()
        result = {"initialized": True}

    # collect는 raw 파일 수집까지만 수행하고, load는 이미 수집한 raw를 DB에 적재합니다.
    # 둘을 분리했기 때문에 네트워크 없이 저장된 파일로 적재를 다시 시도할 수 있습니다.
    elif args.command == "collect":
        from .collect import collect_day

        result = collect_day(args.date)
    elif args.command == "load":
        from .pipeline import load_day

        result = load_day(args.date, fail_before_commit=args.inject_failure)

    # demo는 여러 단계를 조합한 시연, verify는 현재 DB 정답 검증, report는 현재 결과의 스냅샷 생성입니다.
    # 작업 범위가 서로 다르므로 문제를 찾을 때는 개별 명령으로 단계를 좁혀 볼 수 있습니다.
    elif args.command == "demo":
        from .demo import run

        result = run()
    elif args.command == "verify":
        from .pipeline import verify_truth

        result = verify_truth()
    elif args.command == "report":
        from .report import build_report

        result = build_report()
    elif args.command == "benchmark":
        from .benchmark import run

        result = run(args.rows, args.repeats)
    elif args.command == "profile":
        from .profile import run

        result = run()
    elif args.command == "search-lab":
        from .search import run

        result = run()
    elif args.command == "storage-lab":
        from .storage import run

        result = run()
    elif args.command == "semantic-lab":
        from .semantic import run

        result = run()
    elif args.command == "sql-lab":
        from .sql_lab import run

        result = run()
    elif args.command == "dashboard":
        from .dashboard import serve

        return serve(args.port)

    # 결과 json으로 출력

    # ensure_ascii=False로 한글을 읽기 쉽게 출력하고 indent=2로 구조를 드러냅니다.
    # default=str는 기본 JSON 타입이 아닌 값은 문자열로 표시하는 CLI 출력용 기본 처리입니다.
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
