"""13. 데이터 검색
저장된 데이터를 조건에 따라 검색하고 필요한 정보를 찾는 기능을 실습합니다."""

import hashlib
import re
import time
import unicodedata
import numpy as np
from .db import connect
from .config import REPORTS
from .generate import write_json


# 이 이름은 문자 n-gram 해시 알고리즘과 차원을 식별합니다.
# 상품과 질의가 같은 방식으로 만들어진 벡터인지 제한할 때 embedding_model과 비교합니다.
MODEL = "char-ngram-sha256-v1-128"


# 학습 모델 없이 문자 2/3-gram을 해시 버킷에 누적합니다. 해시 충돌이 가능하며 문장 의미를 학습하는 임베딩과 다릅니다.
def embed(text, dim=128):

    # NFKC와 casefold로 표기를 정리한 뒤 연속 공백을 한 칸으로 줄입니다.
    # 문자 표현 차이에 따른 불필요한 벡터 차이를 줄이는 전처리입니다.
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\s+", " ", text).strip()

    # 128개 float32 칸을 0으로 시작합니다.
    # 각 문자 조각은 해시로 선택된 칸에 +1 또는 -1을 더합니다.
    vector = np.zeros(dim, dtype=np.float32)

    # 두 글자와 세 글자 조각을 모두 사용합니다.
    # 예: "텀블러"는 2-gram "텀블", "블러"와 3-gram "텀블러"가 생깁니다.
    for n in (2, 3):
        for i in range(max(0, len(text) - n + 1)):
            token = text[i : i + n].encode()

            # 같은 조각은 같은 SHA-256 bytes를 만듭니다.
            # 앞 4바이트를 정수로 읽고 % dim으로 칸을 정하며, 다음 바이트의 홀짝으로 부호를 정합니다.
            # 서로 다른 조각이 같은 칸으로 들어가는 해시 충돌이 있을 수 있습니다.
            hash_bytes = hashlib.sha256(token).digest()
            vector[int.from_bytes(hash_bytes[:4], "big") % dim] += (
                1 if hash_bytes[4] % 2 else -1
            )
    # 벡터 길이를 1로 맞추면 내적이 코사인 유사도와 같아집니다. 영벡터는 정규화할 수 없어 예외 처리합니다.
    norm = np.linalg.norm(vector)

    # 조각이 없거나 누적 값이 상쇄되어 영벡터가 되면 길이로 나눌 수 없습니다.
    # 일반적으로 한 글자 이하 입력에는 2/3-gram이 없으므로 이 예외에 도달합니다.
    if not norm:
        raise ValueError("검색어는 최소 두 글자 이상이어야 합니다")
    return vector / norm


# 수치 배열을 pgvector 입력 형식 [x,y,...]으로 변환합니다. SQL에는 이 문자열도 파라미터 값으로 전달합니다.
def vector_literal(vector):

    # 각 성분을 float 문자열로 만들고 쉼표로 이어 pgvector 리터럴 형태로 바꿉니다.
    # 예: [0.1,-0.2,0.3]. 실제 SQL에는 %s 값으로 전달하고 ::vector로 변환합니다.
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


# 카테고리와 임베딩 모델을 먼저 제한하고 코사인 거리 순으로 k개를 반환합니다. 동점은 product_id로 정렬합니다.
def search_products(query, category=None, k=5, dsn=None):

    # 너무 많거나 0개 이하인 결과 요청을 거부합니다.
    # 카테고리 None은 모든 카테고리를 의미하며 SQL에서 IS NULL 조건으로 처리합니다.
    if not 1 <= k <= 100:
        raise ValueError("k must be 1..100")
    vector = vector_literal(embed(query))
    with connect(dsn) as conn:
        # 작은 상품 목록에서는 필터 후 정확 검색이 누락 없이 이해하기 쉽습니다.
        # ANN 인덱스의 필터 후 top-k 부족 문제는 아래 별도 실험에서 다룹니다.
        return conn.execute(
            """WITH filtered AS MATERIALIZED (
            SELECT * FROM products WHERE embedding_model=%s AND (%s::text IS NULL OR category=%s)
          ) SELECT product_id,title,category,1-(embedding <=> %s::vector) similarity
          FROM filtered ORDER BY embedding <=> %s::vector,product_id LIMIT %s""",
            (MODEL, category, category, vector, vector, k),
        ).fetchall()


# 정확 검색 top-5를 정답으로 두고 근사 인덱스의 회수율과 시간을 비교합니다. 임시 테이블은 32차원으로 고정되어 있습니다.
def ann_experiment(dsn=None, n=5000, dim=32):

    # 고정 seed로 무작위 데이터 벡터와 질의 벡터를 만듭니다.
    # axis=1은 각 행 벡터, keepdims=True는 (n,1) 모양을 유지해 행별 나눗셈이 되게 합니다.
    rng = np.random.default_rng(42)
    vectors = rng.normal(size=(n, dim)).astype("float32")
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    queries = rng.normal(size=(20, dim)).astype("float32")
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)
    result = []
    with connect(dsn) as conn:
        conn.execute(
            "CREATE TEMP TABLE ann_lab(id integer PRIMARY KEY,embedding vector(32)) ON COMMIT DROP"
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ann_lab VALUES (%s,%s::vector)",
                [(i, vector_literal(v)) for i, v in enumerate(vectors)],
            )

        # 테이블 통계를 갱신한 뒤 인덱스 없는 정확 검색 결과와 시간을 먼저 확보합니다.
        # 이후 만든 근사 인덱스가 그 정답 5개를 얼마나 다시 찾는지 비교합니다.
        conn.execute("ANALYZE ann_lab")
        # 근사 인덱스를 만들기 전에 정확 검색 결과를 확보합니다. ANN 결과와 비교할 기준 집합입니다.
        truth = []
        exact_times = []
        for q in queries:
            start = time.perf_counter()
            hits = conn.execute(
                "SELECT id FROM ann_lab ORDER BY embedding <=> %s::vector LIMIT 5",
                (vector_literal(q),),
            ).fetchall()
            exact_times.append((time.perf_counter() - start) * 1000)
            truth.append({r["id"] for r in hits})

        # HNSW는 탐색 후보 수 ef_search, IVFFlat은 방문할 묶음 수 probes를 바꿉니다.
        # 보통 더 많이 탐색하면 회수율과 시간이 함께 달라질 수 있으므로 직접 측정합니다.
        for method, settings in [("hnsw", [10, 40, 100]), ("ivfflat", [1, 5, 10])]:
            start = time.perf_counter()
            conn.execute(
                f"CREATE INDEX ann_idx ON ann_lab USING {method}(embedding vector_cosine_ops)"
                + (" WITH (lists=50)" if method == "ivfflat" else "")
            )
            build = time.perf_counter() - start

            # 기본 플래너가 선택한 계획을 먼저 기록합니다.
            # 이후 enable_seqscan=off는 인덱스 경로 실험을 위해 순차 스캔을 억제하는 설정이며 운영 권장값이 아닙니다.
            natural = conn.execute(
                "EXPLAIN (FORMAT JSON) SELECT id FROM ann_lab ORDER BY embedding <=> %s::vector LIMIT 5",
                (vector_literal(queries[0]),),
            ).fetchone()
            # 強制는 실험에서만 사용. 자연 계획은 위에 별도로 기록합니다.
            conn.execute("SET LOCAL enable_seqscan=off")
            for setting in settings:

                # 메서드별 설정 이름은 코드 내부의 허용 값에서만 고릅니다.
                # SET LOCAL은 현재 트랜잭션에만 설정을 적용하므로 연결 밖으로 전역 설정을 바꾸지 않습니다.
                parameter = "hnsw.ef_search" if method == "hnsw" else "ivfflat.probes"
                conn.execute(f"SET LOCAL {parameter}={setting}")
                recalls = []
                durations = []

                # zip으로 질의와 해당 질의의 정확 정답 집합을 짝짓습니다.
                # 검색 시간과 정답 ID 교집합 비율을 각 질의마다 기록합니다.
                for q, target in zip(queries, truth):
                    start = time.perf_counter()
                    hits = conn.execute(
                        "SELECT id FROM ann_lab ORDER BY embedding <=> %s::vector LIMIT 5",
                        (vector_literal(q),),
                    ).fetchall()
                    durations.append((time.perf_counter() - start) * 1000)
                    # recall@5 = 정확 검색의 5개 ID 중 근사 검색에서도 찾은 비율입니다. 상품 의미 관련성 점수와 다릅니다.
                    recalls.append(len({r["id"] for r in hits} & target) / 5)
                result.append(
                    {
                        "method": method,
                        "setting": setting,
                        "recall_at_5": float(np.mean(recalls)),
                        "median_ms": float(np.median(durations)),
                        "build_seconds": build,
                        "natural_plan": natural,
                    }
                )

            # 한 방식의 실험이 끝나면 인덱스를 제거해 다음 방식과 섞이지 않게 합니다.
            # 임시 테이블은 ON COMMIT DROP이므로 트랜잭션 종료 시 제거됩니다.
            conn.execute("DROP INDEX ann_idx")
            conn.execute("SET LOCAL enable_seqscan=on")
    return {
        "rows": n,
        "dimensions": dim,
        "queries": len(queries),
        "k": 5,
        "seed": 42,
        "exact_median_ms": float(np.median(exact_times)),
        "results": result,
        "scope": "무작위 벡터의 ANN 회수율. 상품 검색 관련성 평가와 다름. ANN 비교 시 seqscan만 실험적으로 끔.",
    }


# 상품에 문자 해시 벡터를 저장한 뒤 텍스트·JSONB·벡터 검색을 시연하고 별도의 무작위 벡터 ANN 실험을 실행합니다.
def run(dsn=None):
    with connect(dsn) as conn:
        products = conn.execute(
            "SELECT product_id,title,description FROM products ORDER BY product_id"
        ).fetchall()

        # 상품 제목과 설명을 연결해 문자 해시 임베딩을 다시 계산합니다.
        # 상품 ID로 해당 행의 embedding과 embedding_model을 함께 갱신합니다.
        for p in products:
            conn.execute(
                "UPDATE products SET embedding=%s::vector,embedding_model=%s WHERE product_id=%s",
                (
                    vector_literal(embed(p["title"] + " " + p["description"])),
                    MODEL,
                    p["product_id"],
                ),
            )
        conn.execute("ANALYZE products")
        q = vector_literal(embed("휴대용 보온 물병"))

        # 하나의 조회에서 category 조건, JSONB color 포함 조건, 텍스트 토큰 일치를 적용합니다.
        # 그 후보를 코사인 거리로 정렬합니다. <=>는 코사인 거리이며 1-거리는 유사도 표현입니다.
        triple = conn.execute(
            """SELECT product_id,title,ts_rank(search_text,plainto_tsquery('simple',%s)) text_rank,
           1-(embedding <=> %s::vector) similarity FROM products
           WHERE category=%s AND metadata @> %s::jsonb
             AND search_text @@ plainto_tsquery('simple',%s)
           ORDER BY embedding <=> %s::vector LIMIT 5""",
            ("보온", q, "생활", '{"color":"black"}', "보온", q),
        ).fetchall()
        indexes = conn.execute(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='shop' AND tablename='products'"
        ).fetchall()
    hits = search_products("휴대용 보온 물병", "생활", dsn=dsn)
    q = embed("휴대용 보온 물병")
    other = embed("보온 텀블러 물병")

    # q와 other는 단위 벡터이므로 dot(q, other)는 코사인 유사도입니다.
    # L2는 유클리드 거리입니다. 유사도는 클수록, 거리는 작을수록 가깝다는 방향 차이를 확인하세요.
    distances = {
        "cosine_distance": float(1 - np.dot(q, other)),
        "l2_distance": float(np.linalg.norm(q - other)),
        "inner_product": float(np.dot(q, other)),
    }
    report = {
        "embedding_model": MODEL,
        "neural_semantic_embedding": False,
        "limitations": "문자 n-gram 해시 기준선. 같은 표현의 부분 일치에는 유용하지만 동의어 의미를 학습한 모델이 아님.",
        "query": "휴대용 보온 물병",
        "category": "생활",
        "hits": hits,
        "triple_search": triple,
        "indexes": indexes,
        "distances": distances,
        "ann": ann_experiment(dsn),
    }
    write_json(REPORTS / "search.json", report)
    return {"hits": hits, "ann_results": report["ann"]["results"], "model": MODEL}
