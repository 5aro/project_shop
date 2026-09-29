-- 먼저 자식 테이블을 주문 단위로 집계해야 JOIN에서 금액이 곱해지지 않습니다.
CREATE OR REPLACE VIEW order_finance AS
-- 예: 결제 2건과 배송 2건을 그대로 JOIN하면 4행입니다. 두 자식 집합을 먼저 주문당 1행으로 줄입니다.

-- ---------------------------------------------------------
-- 1. 주문별 정산 뷰
-- WITH는 이름 붙인 중간 결과(CTE)를 정의합니다.
-- p는 주문당 결제 합계, s는 주문당 배송 지연 여부입니다.
-- 뷰는 SELECT 정의를 저장하며 별도 데이터를 복사해 저장하는 테이블과 다릅니다.
-- ---------------------------------------------------------
WITH p AS (
 SELECT order_id,

   -- FILTER는 해당 종류의 금액만 SUM에 전달합니다.
   -- 환불이 하나도 없으면 SUM은 NULL일 수 있으므로 COALESCE(...,0)로 0을 씁니다.
   -- 그룹 키가 order_id이므로 분할 결제도 주문별 한 행으로 줄어듭니다.
   COALESCE(SUM(amount_krw) FILTER (WHERE kind='capture'),0) AS captured_krw,
   COALESCE(SUM(amount_krw) FILTER (WHERE kind='refund'),0) AS refunded_krw
 FROM payments GROUP BY order_id
), s AS (

 -- bool_or는 그룹 안에서 하나라도 true이면 true입니다.
 -- 배송 완료가 약속보다 늦은 이벤트가 하나라도 있으면 해당 주문을 late로 표시합니다.
 SELECT order_id, bool_or(delivered_at > promised_at) AS late FROM shipments GROUP BY order_id
)
-- LEFT JOIN은 결제/배송이 없는 주문도 남깁니다. COALESCE는 없는 합계를 0으로 바꾸고 bool_or는 한 건이라도 지연되면 true입니다.
SELECT o.*, COALESCE(p.captured_krw,0) AS captured_krw,
 COALESCE(p.refunded_krw,0) AS refunded_krw,
 COALESCE(p.captured_krw,0)-COALESCE(p.refunded_krw,0) AS net_collected_krw,
 s.order_id IS NOT NULL AS delivered, COALESCE(s.late,false) AS late,
 COALESCE(p.captured_krw,0) <> o.expected_krw AS mismatch

-- orders의 모든 주문을 보존하고 결제/배송 요약을 붙입니다.
-- USING(order_id)는 두 테이블의 같은 이름 열로 동등 조인하는 축약 문법입니다.
-- 결제나 배송이 없으면 대응하는 오른쪽 열은 NULL입니다.
FROM orders o LEFT JOIN p USING(order_id) LEFT JOIN s USING(order_id);

-- 달력으로 빈 날짜를 채워야 7행 이동평균이 7일을 의미합니다. 초반에는 존재하는 날짜 수만큼 평균을 계산합니다.
CREATE OR REPLACE VIEW daily_trends AS

-- ---------------------------------------------------------
-- 2. 빈 날짜를 채운 추세
-- generate_series가 최소~최대 날짜를 하루씩 생성합니다.
-- 중간 거래가 없는 날도 채워야 다음 윈도우의 7행이 7일이 됩니다.
-- 마트가 비어 있으면 이 날짜 범위도 만들어지지 않습니다.
-- ---------------------------------------------------------
WITH calendar AS (
 SELECT generate_series(MIN(business_date),MAX(business_date),'1 day')::date AS business_date FROM mart_daily
), filled AS (

 -- 달력에서 시작해 마트를 LEFT JOIN하므로 마트에 없는 날짜도 남습니다.
 -- 이 실험은 빈 날짜의 주문 수와 순수납액을 0으로 정의합니다.
 SELECT c.business_date, COALESCE(m.order_count,0) AS order_count,
   COALESCE(m.net_collected_krw,0) AS net_collected_krw
 FROM calendar c LEFT JOIN mart_daily m USING(business_date)
)
-- LAG는 이전 행 값을 가져옵니다. NULLIF(이전 값,0)는 0 나눗셈을 막아 증감률을 NULL로 둡니다.
SELECT *, LAG(net_collected_krw) OVER w AS previous_day_krw,

 -- 현재 행 포함 이전 6행까지, 최대 7일 평균입니다.
 -- 처음 1~6일에는 이전 6행이 모두 없으므로 존재하는 행만 평균냅니다.
 ROUND(AVG(net_collected_krw) OVER (ORDER BY business_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),2) AS ma7_krw,

 -- 증감률 = 100 × (오늘-어제)/어제입니다.
 -- 100.0으로 소수 연산을 유도하고 어제가 0이면 NULLIF로 분모를 NULL로 만듭니다.
 -- 첫날은 이전 행이 없어 증감률도 NULL입니다.
 ROUND(100.0*(net_collected_krw-LAG(net_collected_krw) OVER w)/NULLIF(LAG(net_collected_krw) OVER w,0),2) AS growth_pct
FROM filled WINDOW w AS (ORDER BY business_date);

-- 고객마다 시각순으로 정렬하고 30분 초과 공백에 1을 표시한 뒤 누적 합으로 세션 번호를 만듭니다. 같은 시각은 event_id로 순서를 고정합니다.
CREATE OR REPLACE VIEW customer_sessions AS

-- ---------------------------------------------------------
-- 3. 고객별 클릭 세션 복원
-- PARTITION BY customer_id는 고객마다 별도 창을 만듭니다.
-- LAG로 바로 이전 클릭 시각을 가져오고 첫 클릭의 prev는 NULL입니다.
-- 같은 시각 클릭은 event_id로 순서를 고정합니다.
-- ---------------------------------------------------------
WITH previous AS (
 SELECT *, LAG(occurred_at) OVER (PARTITION BY customer_id ORDER BY occurred_at,event_id) AS prev
 FROM click_events
), flagged AS (

 -- 첫 클릭 또는 이전 클릭 후 30분 초과 공백이면 새 세션 표시 1입니다.
 -- 정확히 30분이면 0이므로 같은 세션입니다. 그 경계를 테스트에서도 확인합니다.
 SELECT *, CASE WHEN prev IS NULL OR occurred_at-prev > interval '30 minutes' THEN 1 ELSE 0 END AS new_session
 FROM previous
)
SELECT event_id,customer_id,occurred_at,action,

 -- 새 세션 표시를 고객별 첫 행부터 현재 행까지 누적합니다.
 -- 예: 표시 [1,0,1,0] → 세션 번호 [1,1,2,2].
 -- 세션 번호는 고객 안에서만 고유하므로 전체 세션 집계는 고객 ID와 함께 사용합니다.
 SUM(new_session) OVER (PARTITION BY customer_id ORDER BY occurred_at,event_id ROWS UNBOUNDED PRECEDING) AS session_id
FROM flagged;

