
-- 각 예제를 따로 실행하며 결과를 확인하세요.
-- GROUP BY는 행을 그룹별로 줄이고 OVER 윈도우 함수는 현재 행을 유지한 채 주변 정보를 계산합니다.
SET search_path TO shop,public;
-- 1. 기초 조회: 주문 당시 단가가 2만 원 이상인 최근 주문

-- WHERE로 조건을 적용하고 ORDER BY로 최근 순으로 정렬한 뒤 LIMIT 10으로 자릅니다.
-- 같은 ordered_at이면 order_id로 순서를 결정해 결과가 흔들리지 않게 합니다.
SELECT order_id,quantity,unit_price_krw FROM orders WHERE unit_price_krw>=20000 ORDER BY ordered_at DESC,order_id LIMIT 10;
-- 2. HAVING: 주문이 50건 이상인 상품, 0건 상품은 LEFT JOIN으로 유지 가능
-- LEFT JOIN 자체는 0건 상품을 남기지만, 이 쿼리의 HAVING >=50이 최종 결과에서는 제외합니다.
SELECT p.product_id,p.title,COUNT(o.order_id) orders FROM products p LEFT JOIN orders o USING(product_id)

-- WHERE는 집계 전 행 조건, HAVING은 집계 후 그룹 조건입니다.
-- COUNT(o.order_id)는 NULL을 제외하므로 주문 없는 상품은 0건이지만 HAVING에서 제외됩니다.
GROUP BY p.product_id HAVING COUNT(o.order_id)>=50 ORDER BY orders DESC;
-- 3. 순위 함수: 동점 처리 방식 비교
-- ROW_NUMBER는 고유 순번, RANK는 동점 뒤 순번 건너뛰기, DENSE_RANK는 건너뛰지 않기입니다.
SELECT product_id,net,

 -- 예: 순수납액 [100,100,50]일 때
 -- ROW_NUMBER=[1,2,3], RANK=[1,1,3], DENSE_RANK=[1,1,2]입니다.
 -- ROW_NUMBER에만 product_id를 추가해 같은 금액의 표시 순서를 고정합니다.
 ROW_NUMBER() OVER(ORDER BY net DESC,product_id) row_number,
 RANK() OVER(ORDER BY net DESC) rank,
 DENSE_RANK() OVER(ORDER BY net DESC) dense_rank
FROM (SELECT product_id,SUM(net_collected_krw) net FROM order_finance GROUP BY product_id) p;
-- 4. 채널별 비중: 행은 유지하고 전체 합계를 옆에 붙임
-- GROUP BY로 채널별 COUNT를 만든 후 SUM(COUNT(*)) OVER()로 그룹 전체 합계를 각 행에 붙입니다.
SELECT channel,COUNT(*) orders,ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER(),2) share_pct FROM orders GROUP BY channel;
-- 5. 주문 코호트와 구별되는 결제 발생일별 순수납액

-- AT TIME ZONE은 UTC로 해석된 실제 순간을 한국 현지 시각으로 나타냅니다.
-- ::date로 시간을 버린 뒤 결제 발생일별 합계를 만듭니다. 주문일별 마트와 값의 날짜 배치가 다릅니다.
SELECT (occurred_at AT TIME ZONE 'Asia/Seoul')::date cash_date,

 -- CASE는 종류에 따라 더할 금액의 부호를 바꿉니다.
 -- 환불 행 자체는 양수로 저장했으므로 여기서 -amount_krw로 빼 줍니다.
 SUM(CASE WHEN kind='capture' THEN amount_krw ELSE -amount_krw END) net_krw
FROM payments GROUP BY 1 ORDER BY 1;
-- 6. 지연 도착: event time과 receipt time을 따로 분석
SELECT kind,COUNT(*) FILTER(WHERE received_at-occurred_at>interval '24 hours') delayed FROM payments GROUP BY kind;
-- 7. 세션 복원 결과: 30분 초과 무활동이면 새 세션

-- 같은 고객/세션 조합별 이벤트 수와 처음~마지막 시각 차이를 계산합니다.
-- 이 duration은 관측된 클릭 간 범위이며 실제 사용자가 화면을 보고 있던 모든 시간을 뜻하지 않습니다.
SELECT customer_id,session_id,COUNT(*) events,MAX(occurred_at)-MIN(occurred_at) duration
FROM customer_sessions GROUP BY 1,2 ORDER BY 1,2 LIMIT 20;
-- 8. 날짜 spine을 사용하여 거래 없는 날도 포함한 7일 이동평균
SELECT * FROM daily_trends ORDER BY business_date;
-- 9. JSONB 연산자, 키 존재, 중첩 조회
-- ->>는 속성을 텍스트로, #>>는 경로의 값을 텍스트로 추출합니다. @>는 JSON 포함, ?는 키 존재 조건입니다.
SELECT product_id,metadata->>'color' color,metadata#>>'{tags,0}' first_tag
FROM products WHERE metadata @> '{"color":"black"}' AND metadata ? 'tags';
-- 10. LEAD: 다음 주문까지의 간격 (재방문 여부는 실제 날짜 차이로 판단)

-- LEAD는 같은 고객의 다음 행을 가져옵니다. 마지막 주문은 다음 행이 없어 NULL입니다.
-- 여기 LIMIT 20에는 바깥 ORDER BY가 없으므로 어떤 20행이 표시될지는 보장되지 않습니다.
SELECT customer_id,ordered_at,LEAD(ordered_at) OVER(PARTITION BY customer_id ORDER BY ordered_at,order_id)-ordered_at AS next_order_gap
FROM orders LIMIT 20;
