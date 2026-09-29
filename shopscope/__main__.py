# python -m shopscope가 이 파일을 실행합니다. 실제 인자 처리와 기능 분기는 cli.main에 위임합니다.
from .cli import main

if __name__ == "__main__":
    main()
