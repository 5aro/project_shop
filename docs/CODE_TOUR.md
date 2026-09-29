# 초급자를 위한 코드 읽기

한 번에 모든 라이브러리를 이해할 필요는 없습니다. 먼저 주문 한 건이 어떻게 결과로 바뀌는지 따라갑니다.

1. `shopscope/config.py`: 프로젝트 경로와 접속 주소를 읽습니다. 환경 변수는 코드 수정 없이 설정을 바꾸는 방법입니다.
2. `generate.py`: 주문 딕셔너리를 만드는 부분을 읽습니다. 함수 입력·반환값, for, list, dict를 확인합니다. seed가 같으면 같은 데이터를 생성합니다.
3. `source.py`와 `collect.py`: HTTP 응답의 JSON이 Python 객체가 되고 JSONL 파일로 저장되는 흐름을 읽습니다. 페이지 번호와 재시도 횟수를 구분합니다.
4. `quality.py`: 정상 행과 잘못된 수량 행을 비교합니다. 오류를 감추지 않고 사유를 붙여 격리하는 이유를 설명합니다.
5. `sql/001_schema.sql`: products → orders → payments 관계를 그림으로 그립니다. PK는 식별, FK는 존재하는 부모 참조, CHECK는 값의 제약입니다.
6. `pipeline.py`: `load_day`의 검증→트랜잭션→체크포인트 순서를 읽습니다. with 블록 안에서 예외가 나면 왜 일부 INSERT만 남지 않는지 확인합니다.
7. `sql/002_marts.sql`: SUM을 먼저 하고 JOIN하는 이유를 공부합니다. 주문 1개에 결제 2개·배송 2개를 바로 JOIN하면 4행이 된다는 예를 손으로 계산합니다.
8. `report.py`: SQL 결과가 JSON으로 바뀌고 `web/data.js`로 전달되는 과정을 확인합니다. 대시보드는 DB를 직접 수정하지 않습니다.
9. `tests/test_pipeline.py`: 각 테스트의 입력, 실행, 기대값 세 부분을 표시합니다. 테스트 통과는 작성한 조건의 검증이지 모든 오류가 없다는 뜻은 아닙니다.

## 직접 확인할 SQL

```sql
SET search_path TO shop, public;
SELECT * FROM orders ORDER BY ordered_at LIMIT 1;
SELECT order_id, COUNT(*), SUM(amount_krw)
FROM payments GROUP BY order_id ORDER BY COUNT(*) DESC LIMIT 5;
SELECT SUM(captured_krw), SUM(refunded_krw), SUM(net_collected_krw)
FROM order_finance;
SELECT source, reason, COUNT(*) FROM quarantine GROUP BY source, reason;
```

컬럼의 정확한 이름은 스키마를 함께 확인합니다. 결제 합계에는 capture와 refund 구분이 필요하므로 두 번째 SQL의 단순 SUM을 순수납액으로 해석하면 안 됩니다.

## 이해 점검과 답

- 중복 주문이 두 번 집계되지 않는 이유: 고유 키와 이벤트 해시, 처리 배치 체크포인트가 재전송을 통제합니다.
- 늦은 환불 때문에 과거 값이 달라지는 이유: 주문일 기준으로 지금까지 수신한 환불을 반영하기 때문입니다.
- 오류 행을 그냥 삭제하지 않는 이유: 원인을 확인하고 재처리할 근거가 필요하기 때문입니다.
- Redis 데이터가 없어졌을 때: 기준 데이터는 PostgreSQL에 있으므로 캐시를 재생성합니다.
- 벤치마크에서 Dask가 느릴 수 있는 이유: 작은 데이터에서는 분산 작업 준비 비용이 실제 계산보다 클 수 있습니다.
