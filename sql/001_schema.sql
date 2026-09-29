-- shop은 테이블의 이름 공간입니다. search_path는 접두사 없이 테이블을 참조할 때 탐색할 스키마 순서입니다.

-- ---------------------------------------------------------
-- 1. 이름 공간과 확장 준비
-- 스키마는 관련 테이블을 묶는 DB 내부 이름 공간입니다.
-- shop.orders처럼 명시할 수 있고, search_path를 설정하면 orders로 줄여 쓸 수 있습니다.
-- IF NOT EXISTS는 반복 실행 시 이미 있는 객체를 다시 만들지 않습니다.
-- ---------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS shop;
SET search_path TO shop, public;
-- pgvector 확장이 vector 타입과 거리 연산자, 근사 검색 인덱스를 제공합니다.
CREATE EXTENSION IF NOT EXISTS vector;

-- 정수 원화: 부동소수점 반올림으로 정산 금액이 달라지지 않게 합니다.
-- 상품은 주문의 부모 테이블입니다. JSONB는 가변 속성, tsvector는 텍스트 검색, vector는 임베딩 검색에 사용합니다.
CREATE TABLE IF NOT EXISTS products (

  -- PRIMARY KEY는 NULL과 중복을 허용하지 않는 대표 식별자입니다.
  -- NOT NULL은 누락 방지, CHECK(price_krw > 0)는 값의 범위 제약입니다.
  -- JSONB metadata에는 color/tags처럼 상품마다 유연한 속성을 넣습니다.
  product_id text PRIMARY KEY, title text NOT NULL, category text NOT NULL,
  seller_id text NOT NULL, price_krw bigint NOT NULL CHECK(price_krw > 0),
  description text NOT NULL, metadata jsonb NOT NULL,
  embedding vector(128), embedding_model text,

  -- title과 description을 합쳐 텍스트 검색용 토큰으로 변환합니다.
  -- GENERATED ALWAYS ... STORED는 상품 변경 시 DB가 값을 계산해 저장한다는 뜻입니다.
  -- 'simple' 사전은 언어별 의미 분석 모델이 아니므로 한국어 의미 검색과 구별하세요.
  search_text tsvector GENERATED ALWAYS AS
    (to_tsvector('simple', title || ' ' || description)) STORED
);
-- B-tree는 category 동등 조건, GIN은 JSON 포함/텍스트 토큰, HNSW는 벡터 근사 검색을 위한 서로 다른 자료구조입니다.
CREATE INDEX IF NOT EXISTS products_category_idx ON products(category);

-- GIN의 jsonb_path_ops는 JSON 포함 조건(@>)에 맞춘 연산자 클래스입니다.
-- metadata의 모든 종류의 질의에 이 인덱스가 사용된다는 뜻은 아닙니다.
CREATE INDEX IF NOT EXISTS products_meta_idx ON products USING gin(metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS products_search_idx ON products USING gin(search_text);

-- HNSW는 가까운 벡터를 빠르게 찾기 위한 근사 인덱스입니다.
-- vector_cosine_ops는 코사인 거리 연산자 <=>에 대응합니다.
-- 인덱스가 존재해도 데이터 크기/쿼리 형태에 따라 플래너가 다른 경로를 선택할 수 있습니다.
CREATE INDEX IF NOT EXISTS products_vector_idx ON products USING hnsw(embedding vector_cosine_ops);

-- PRIMARY KEY는 식별자 중복을, REFERENCES는 없는 상품 참조를 막습니다. 주문 당시 단가를 저장해 상품 가격 변경과 분리합니다.
-- expected_krw는 DB가 계산하는 저장 생성 열입니다. 앱이 별도로 값을 넣지 않아도 수량×단가가 유지됩니다.
CREATE TABLE IF NOT EXISTS orders (

  -- 이 학습 모델은 주문 한 건에 상품 한 종류만 저장합니다.
  -- 실제 여러 상품 주문이라면 주문 헤더와 주문 항목 테이블을 나누는 설계가 필요할 수 있습니다.
  order_id text PRIMARY KEY, customer_id text NOT NULL,
  product_id text NOT NULL REFERENCES products, channel text NOT NULL,

  -- timestamptz는 시간대가 있는 입력의 실제 순간을 저장합니다.
  -- 조회 표시와 날짜로 자르는 기준은 세션 시간대/AT TIME ZONE에 따라 달라집니다.
  ordered_at timestamptz NOT NULL, quantity integer NOT NULL CHECK(quantity BETWEEN 1 AND 100),
  unit_price_krw bigint NOT NULL CHECK(unit_price_krw > 0),

  -- quantity를 bigint로 바꾼 뒤 곱합니다.
  -- 예: 수량 2 × 주문 당시 단가 10,000 = 기대 금액 20,000원입니다.
  -- 가격이 바뀌어도 orders.unit_price_krw가 남아 과거 주문 금액은 유지됩니다.
  expected_krw bigint GENERATED ALWAYS AS (quantity::bigint * unit_price_krw) STORED,
  payload_hash text NOT NULL, metadata jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_date_idx ON orders(ordered_at);

-- 복합 인덱스의 열 순서는 customer_id, ordered_at입니다.
-- 고객을 고른 뒤 해당 고객의 시각 순서/범위를 찾는 질의와 연결해 생각하세요.
CREATE INDEX IF NOT EXISTS orders_customer_idx ON orders(customer_id, ordered_at);

-- 이벤트 원장은 불변입니다. 같은 ID + 다른 내용은 수정하지 않고 격리합니다.
-- capture와 refund는 모두 양수 금액으로 기록하고 집계에서 구분합니다. 불변 원장 정책은 put_row에서 구현하며 SQL UPDATE 자체를 막는 트리거는 없습니다.
CREATE TABLE IF NOT EXISTS payments (
  event_id text PRIMARY KEY, order_id text NOT NULL REFERENCES orders,

  -- 금액 부호 대신 종류로 수납과 환불을 구별합니다.
  -- capture 10,000과 refund 3,000을 저장하면 순수납액은 집계에서 7,000으로 계산합니다.
  kind text NOT NULL CHECK(kind IN ('capture','refund')),
  amount_krw bigint NOT NULL CHECK(amount_krw > 0),

  -- occurred_at은 업무상 발생 시각, received_at은 원천에서 수신된 시각입니다.
  -- 두 값의 차이는 늦게 도착한 이벤트를 분석하는 근거입니다.
  occurred_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
  payload_hash text NOT NULL, metadata jsonb NOT NULL,

  -- 애플리케이션 검증을 통과하더라도 DB가 이 시간 제약을 다시 지킵니다.
  -- 다만 주문보다 결제가 빠른지는 다른 테이블을 봐야 하므로 check_finance에서 검사합니다.
  CHECK(received_at >= occurred_at)
);
CREATE INDEX IF NOT EXISTS payments_order_idx ON payments(order_id);
-- 배송 완료 이벤트 모델입니다. 아직 도착하지 않은 배송은 행이 없으며, 완료 시각과 약속 시각 비교로 지연을 판정합니다.
CREATE TABLE IF NOT EXISTS shipments (
  event_id text PRIMARY KEY, order_id text NOT NULL REFERENCES orders,
  carrier text NOT NULL, promised_at timestamptz NOT NULL,
  delivered_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
  payload_hash text NOT NULL, CHECK(received_at >= delivered_at)
);
CREATE INDEX IF NOT EXISTS shipments_order_idx ON shipments(order_id);

-- 원천 종류+내용 해시를 복합 키로 삼아 같은 원문을 한 번만 보존합니다. 최초로 관측한 배치도 추적합니다.
CREATE TABLE IF NOT EXISTS raw_events (

  -- 복합 기본키 (source,payload_hash)는 원천 종류와 내용이 모두 같은 행을 중복 보관하지 않습니다.
  -- 파일 raw는 원래 수신 형태를 보관하고 이 테이블은 DB 트랜잭션 내 원문 추적에 사용됩니다.
  source text NOT NULL, payload_hash text NOT NULL, payload jsonb NOT NULL,
  first_batch text NOT NULL, PRIMARY KEY(source,payload_hash)
);
-- 격리는 삭제가 아닙니다. 원문과 사유를 보존하며 부모 주문이 생긴 뒤 재검사에 성공하면 resolved로 전환합니다.
CREATE TABLE IF NOT EXISTS quarantine (
  source text NOT NULL, payload_hash text NOT NULL, payload jsonb NOT NULL,
  reason text NOT NULL, first_batch text NOT NULL,

  -- DEFAULT는 INSERT에서 값을 생략했을 때 적용됩니다.
  -- 격리 상태는 open/resolved 두 값만 허용하여 오타가 임의 상태로 저장되지 않게 합니다.
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
  PRIMARY KEY(source,payload_hash)
);
-- 성공한 배치의 체크포인트입니다. 원장/마트와 같은 트랜잭션에서 저장해야 실패한 작업을 완료로 오인하지 않습니다.
CREATE TABLE IF NOT EXISTS processed_batches (

  -- batch_id는 manifest의 데이터 묶음을 식별합니다.
  -- 한 번 성공한 같은 배치를 다시 만나면 이 기록을 근거로 건너뜁니다.
  -- manifest_hash로 같은 이름의 명세가 바뀌었는지도 검사합니다.
  batch_id text PRIMARY KEY, business_date date NOT NULL, manifest_hash text NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(), rows_seen integer NOT NULL
);
-- 배치 처리와 실행 시도는 다릅니다. 같은 배치 재실행에도 매번 run_id가 달라져 시도별 상태와 시간이 남습니다.
CREATE TABLE IF NOT EXISTS pipeline_runs (

  -- run_id는 실행 시도마다 새 UUID입니다.
  -- started_at 기본값 now()는 기록 생성 시각, ended_at은 종료 시 갱신하는 시각입니다.
  run_id text PRIMARY KEY, business_date date NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
  status text NOT NULL, metrics jsonb NOT NULL DEFAULT '{}', error text
);
-- alert_key로 동일 경고를 모으고 occurrences를 늘립니다. 외부 알림 전송이 아닌 로컬 DB 기록입니다.
CREATE TABLE IF NOT EXISTS alerts (
  alert_key text PRIMARY KEY, severity text NOT NULL, message text NOT NULL,
  occurrences integer NOT NULL DEFAULT 1, last_seen timestamptz NOT NULL DEFAULT now()
);
-- 마트는 조회용 요약 테이블입니다. business_date는 주문일이고 전체 재집계로 늦은 환불을 과거 날짜에 반영합니다.
CREATE TABLE IF NOT EXISTS mart_daily (

  -- 날짜당 요약 한 행을 저장합니다.
  -- 지표 열에는 이미 중복 제거된 원장 합계만 들어가야 합니다.
  -- 마트가 틀렸을 때 원장에서 재생성할 수 있도록 원장과 요약을 구분합니다.
  business_date date PRIMARY KEY, order_count bigint NOT NULL,
  ordered_gmv_krw bigint NOT NULL, captured_krw bigint NOT NULL,
  refunded_krw bigint NOT NULL, net_collected_krw bigint NOT NULL,
  delivered_orders bigint NOT NULL, late_orders bigint NOT NULL,
  mismatched_orders bigint NOT NULL
);
-- 고객별 클릭 순서를 세션으로 묶기 위한 입력입니다. 고객 ID는 외부 고객 테이블에 연결하지 않는 합성 식별자입니다.
CREATE TABLE IF NOT EXISTS click_events (
  event_id text PRIMARY KEY, customer_id text NOT NULL,
  occurred_at timestamptz NOT NULL, action text NOT NULL
);

