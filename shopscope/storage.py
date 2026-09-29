"""10. 데이터 저장 방식 실습
동일한 데이터를 여러 저장 방식과 파일 포맷으로 다루면서 차이를 학습합니다."""


# 이 실험은 같은 상품을 저장소별로 어떻게 표현하고 조회하는지 비교합니다.
# MongoDB=문서, Redis=기한이 있는 캐시, Neo4j=노드와 관계입니다.
# 거래 원장과 비교 기준은 PostgreSQL에 남깁니다.
import json
import os
import time
from .db import connect
from .generate import write_json
from .config import REPORTS


# PostgreSQL 조회를 기준으로 문서 DB 조회·캐시 수명·그래프 관계를 실험합니다. 저장소들에 걸친 원자적 커밋은 구현하지 않습니다.
def run():
    import redis
    from pymongo import MongoClient, ReplaceOne
    from neo4j import GraphDatabase

    with connect() as conn:

        # 상품 정보를 딕셔너리 목록으로 가져옵니다.
        # expected는 PostgreSQL에서 생활 카테고리 상품 ID를 정렬한 기준 결과입니다.
        products = conn.execute(
            "SELECT product_id,title,category,seller_id,price_krw,description,metadata FROM products ORDER BY product_id"
        ).fetchall()
        expected = [
            r["product_id"]
            for r in conn.execute(
                "SELECT product_id FROM products WHERE category='생활' ORDER BY product_id"
            )
        ]

    # MongoDB 실험은 연결 실패도 보고서에 blocked로 기록하고 다음 실험을 이어 갑니다.
    # Redis/Neo4j 오류에는 같은 예외 처리 블록이 없으므로 함수가 중단될 수 있습니다.
    mongo_status = "verified"
    mongo_ids = []
    mongo_explain = {}
    mongo_error = None

    # 환경변수가 없으면 로컬 포트 57017로 연결합니다.
    # serverSelectionTimeoutMS=3000은 서버 선택을 최대 3초 기다린다는 뜻입니다.
    mongo = MongoClient(
        os.environ.get("SHOPSCOPE_MONGO", "mongodb://127.0.0.1:57017"),
        serverSelectionTimeoutMS=3000,
    )
    try:

        # shopscope는 데이터베이스, products는 컬렉션입니다.
        # product_id의 unique 인덱스는 중복 ID를 막고 category 인덱스는 조건 검색을 돕습니다.
        collection = mongo.shopscope.products
        collection.create_index("product_id", unique=True)
        collection.create_index("category")
        # product_id 기준 upsert로 문서를 교체합니다. JSONB와 문서 저장의 차이를 같은 상품 데이터로 비교합니다.
        collection.bulk_write(
            [

                # 상품 ID가 있는 문서는 전체를 교체하고 없으면 삽입합니다(upsert=True).
                # 따라서 재실행해도 같은 상품의 문서가 계속 추가되지 않습니다.
                ReplaceOne({"product_id": p["product_id"]}, p, upsert=True)
                for p in products
            ]
        )

        # find의 첫 인자는 조건, 두 번째는 반환 필드 선택입니다.
        # 결과 순서는 DB마다 다를 수 있으므로 ID를 정렬한 뒤 expected와 비교합니다.
        mongo_ids = sorted(
            p["product_id"]
            for p in collection.find({"category": "생활"}, {"product_id": 1})
        )
        assert mongo_ids == expected

        # executionStats는 실제 실행 통계를 포함하는 설명입니다.
        # 단순히 인덱스가 있다는 사실보다 실제 조회 경로와 탐색 건수를 확인하는 데 사용합니다.
        mongo_explain = mongo.shopscope.command(
            "explain",
            {"find": "products", "filter": {"category": "생활"}},
            verbosity="executionStats",
        )

    # MongoDB 연결/조회/결과 불일치까지 이 블록에서 blocked로 기록합니다.
    # 에러 종류와 메시지 일부를 남기고 finally에서 클라이언트 자원을 닫습니다.
    except Exception as exc:
        mongo_status = "blocked"
        mongo_error = type(exc).__name__ + ": " + str(exc)[:160]
    finally:
        mongo.close()

    # decode_responses=True는 Redis bytes 응답을 문자열로 바꿔 줍니다.
    # ping으로 연결을 확인하고 실험 키를 지워 최초 조회가 miss가 되도록 만듭니다.
    cache = redis.Redis.from_url(
        os.environ.get("SHOPSCOPE_REDIS", "redis://127.0.0.1:56379/0"),
        decode_responses=True,
    )
    cache.ping()
    key = "shopscope:v1:products:category:생활"
    cache.delete(key)
    # 캐시 miss → 값 저장 → hit → TTL 만료 → 무효화 순서입니다. 원본은 PostgreSQL이므로 캐시는 다시 만들 수 있습니다.
    first = cache.get(key)

    # setex는 값을 저장하면서 TTL을 초 단위로 함께 지정합니다.
    # 목록을 JSON 문자열로 넣고, 다시 읽을 때 json.loads로 목록을 복원합니다.
    cache.setex(key, 60, json.dumps(expected))
    second = json.loads(cache.get(key))
    ttl = cache.ttl(key)
    assert first is None and second == expected and 0 < ttl <= 60

    # px=100은 100밀리초 만료입니다. 150밀리초 후 None인지 확인해 실제 만료를 관찰합니다.
    # 이 키는 TTL 실험 전용이며 상품 캐시 키와 구분됩니다.
    cache.set("shopscope:ttl-test", "value", px=100)
    time.sleep(0.15)
    assert cache.get("shopscope:ttl-test") is None
    # 更新 후 무효화 실험. 상품 실데이터는 수정하지 않고 캐시 키만 제거합니다.

    # 원본이 갱신되었다고 가정하고 캐시 키를 삭제하는 무효화 단계입니다.
    # TTL만 기다리면 만료 전까지 오래된 값을 볼 수 있으므로 갱신 시 삭제하는 전략을 비교합니다.
    cache.delete(key)
    invalidated = cache.get(key) is None

    # Neo4j는 Bolt 프로토콜 주소로 연결합니다.
    # 인증 값은 환경변수를 우선하며 여기서는 로컬 실습 기본 계정을 제공합니다.
    uri = os.environ.get("SHOPSCOPE_NEO4J", "bolt://127.0.0.1:57687")
    graph = GraphDatabase.driver(
        uri,
        auth=("neo4j", os.environ.get("SHOPSCOPE_NEO4J_PASSWORD", "shopscope_local")),
    )
    # MERGE로 상품·판매자 노드와 SELLS 관계의 중복 생성을 줄입니다. 상품→판매자→다른 상품 조회는 두 관계를 건너갑니다.
    with graph.session() as session:
        session.run(
            "CREATE CONSTRAINT shop_product_id IF NOT EXISTS FOR (p:ShopProduct) REQUIRE p.id IS UNIQUE"
        ).consume()
        session.run(
            "CREATE CONSTRAINT shop_seller_id IF NOT EXISTS FOR (s:ShopSeller) REQUIRE s.id IS UNIQUE"
        ).consume()
        session.run(
            """UNWIND $rows AS row
            MERGE (p:ShopProduct {id:row.product_id}) SET p.title=row.title
            MERGE (s:ShopSeller {id:row.seller_id}) MERGE (s)-[:SELLS]->(p)""",
            rows=products,
        ).consume()

        # Cypher 패턴 (상품)<-[:SELLS]-(판매자)-[:SELLS]->(다른상품)을 따라갑니다.
        # 같은 판매자의 다른 상품을 찾고 WHERE other<>p로 시작 상품을 제외합니다.
        two_hop = [
            dict(r)
            for r in session.run(
                """MATCH (p:ShopProduct {id:$id})<-[:SELLS]-(s:ShopSeller)-[:SELLS]->(other:ShopProduct)
            WHERE other<>p RETURN s.id AS seller,other.id AS product,other.title AS title ORDER BY product""",
                id="P0000",
            )
        ]
        count = session.run("MATCH (p:ShopProduct) RETURN count(p) AS n").single()["n"]
    graph.close()

    # 각 저장소가 검증한 사실을 별도 항목으로 저장합니다.
    # MongoDB가 blocked면 same_results를 False 대신 None으로 두어 미검증과 불일치를 구분합니다.
    report = {
        "postgres_category_count": len(expected),
        "mongo_category_count": len(mongo_ids),
        "same_results": mongo_ids == expected if mongo_status == "verified" else None,
        "mongo": {
            "status": mongo_status,
            "error": mongo_error,
            "documents": len(products) if mongo_status == "verified" else None,
            "execution_stats": mongo_explain.get("executionStats"),
        },
        "redis": {
            "first_miss": first is None,
            "second_hit": second == expected,
            "ttl_seconds": ttl,
            "ttl_expired": True,
            "invalidated": invalidated,
        },
        "neo4j": {"products": count, "two_hop_results": two_hop},
        "decision": "거래 정합성의 기준은 PostgreSQL. MongoDB는 내장 문서 모델 비교, Redis는 재생성 가능한 캐시, Neo4j는 관계 탐색 실험. DB 간 분산 트랜잭션은 구현하지 않음.",
    }
    write_json(REPORTS / "storage.json", report)
    return report
