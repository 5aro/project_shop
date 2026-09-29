"""14. 의미 기반 검색
문자열이 정확히 일치하는 검색을 넘어 데이터의 의미를 이용한 검색 방식을 실습합니다."""

import hashlib
import os
import time
from .db import connect
from .search import vector_literal
from .config import REPORTS
from .generate import write_json

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# 모델 원격 리비전을 고정해 재현성을 높입니다. 로컬 경로를 지정하면 아래 기록이 실제 로컬 모델 버전을 자동 검증하지는 않습니다.
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"


# 다국어 모델로 상품과 질의를 같은 벡터 공간에 놓습니다. 모델 다운로드 또는 로컬 모델과 별도 semantic 의존성이 필요합니다.
def run():
    from sentence_transformers import SentenceTransformer


    # SHOPSCOPE_MODEL_PATH가 있으면 지정한 로컬 모델 경로를 사용합니다.
    # 없으면 MODEL과 고정 REVISION으로 모델을 불러옵니다.
    local = os.environ.get("SHOPSCOPE_MODEL_PATH")
    start = time.perf_counter()

    # device="cpu"는 CPU에서 계산합니다.
    # HF_HUB_OFFLINE=1이면 로컬 파일만 허용하며, trust_remote_code=False는 모델 저장소의 별도 코드를 실행하도록 허용하지 않습니다.
    model = SentenceTransformer(
        local or MODEL,
        revision=None if local else REVISION,
        device="cpu",
        local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
        trust_remote_code=False,
    )
    with connect() as conn:
        rows = conn.execute(
            "SELECT product_id,title,description,category FROM products ORDER BY product_id"
        ).fetchall()

        # 각 상품의 title과 description을 공백으로 연결한 문장 목록입니다.
        # DB 조회 순서와 vectors 행 순서가 일치하도록 같은 rows에서 만들고 그대로 zip합니다.
        texts = [r["title"] + " " + r["description"] for r in rows]

        # 모델이 문장마다 384차원 수치 벡터를 반환합니다.
        # normalize_embeddings=True로 길이를 1로 정규화하며 상품과 질의에 같은 옵션을 적용합니다.
        vectors = model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        # 스키마의 vector(384)와 모델 출력 차원이 일치해야 합니다. 문자 해시 128차원 임베딩과 별도 테이블을 사용합니다.
        assert vectors.shape == (len(rows), 384)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS product_semantics(
           product_id text PRIMARY KEY REFERENCES products, model text NOT NULL,
           revision text NOT NULL, content_hash text NOT NULL,embedding vector(384) NOT NULL)"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS semantic_hnsw ON product_semantics USING hnsw(embedding vector_cosine_ops)"
        )

        # 상품 ID, 모델명, 리비전, 입력 텍스트 해시, 벡터를 함께 저장합니다.
        # content_hash는 어떤 내용으로 만들었는지 추적하는 정보이며, 현재 함수는 해시가 같아도 매번 재계산합니다.
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO product_semantics VALUES (%s,%s,%s,%s,%s::vector)
              ON CONFLICT(product_id) DO UPDATE SET model=excluded.model,revision=excluded.revision,
                content_hash=excluded.content_hash,embedding=excluded.embedding""",
                [
                    (
                        r["product_id"],
                        MODEL,
                        REVISION,
                        hashlib.sha256(t.encode()).hexdigest(),
                        vector_literal(v),
                    )
                    for r, t, v in zip(rows, texts, vectors)
                ],
            )
        results = []
        # 카테고리는 사람이 적어 둔 기대값입니다. filtered 검색에는 정답 카테고리를 직접 넘기므로 일반 검색 평가와 구분해야 합니다.
        examples = [
            ("따뜻한 커피를 담아서 출근할 때 가져가고 싶어요", "생활"),
            ("주변 소음을 줄이고 음악을 듣고 싶어요", "전자"),
            ("노트북을 넣고 출근할 가방", "패션"),
            ("달리기할 때 발이 편한 신발", "스포츠"),
        ]

        # 각 예시에는 사람이 정한 기대 카테고리가 붙어 있습니다.
        # 질의도 같은 모델로 인코딩하고, 전체 후보 검색과 기대 카테고리를 미리 넣은 검색을 각각 실행합니다.
        for query, expected in examples:
            vector = vector_literal(model.encode(query, normalize_embeddings=True))
            hits = conn.execute(
                """SELECT p.product_id,p.title,p.category,1-(s.embedding <=> %s::vector) similarity
                FROM product_semantics s JOIN products p USING(product_id)
                WHERE s.model=%s AND s.revision=%s
                ORDER BY s.embedding <=> %s::vector,p.product_id LIMIT 5""",
                (vector, MODEL, REVISION, vector),
            ).fetchall()

            # MATERIALIZED 후보 집합에서 카테고리를 먼저 제한한 뒤 거리를 계산합니다.
            # 이 예시는 정답 카테고리를 입력에 제공한 조건부 검색이므로 전체 검색 성공률과 같은 평가가 아닙니다.
            filtered = conn.execute(
                """WITH candidates AS MATERIALIZED (
                SELECT p.product_id,p.title,p.category,s.embedding FROM product_semantics s JOIN products p USING(product_id)
                WHERE p.category=%s AND s.model=%s AND s.revision=%s)
                SELECT product_id,title,category,1-(embedding <=> %s::vector) similarity
                FROM candidates ORDER BY embedding <=> %s::vector,product_id LIMIT 3""",
                (expected, MODEL, REVISION, vector, vector),
            ).fetchall()

            # 상위 결과와 top1 카테고리 일치 여부를 함께 저장합니다.
            # bool(hits and ...)는 결과가 없을 때 인덱스 오류 없이 False가 되게 합니다.
            results.append(
                {
                    "query": query,
                    "expected_category": expected,
                    "hits": hits,
                    "filtered_hits": filtered,
                    "top1_category_match": bool(
                        hits and hits[0]["category"] == expected
                    ),
                }
            )

    # 모델/차원/실행 시간과 네 예시를 기록합니다.
    # 측정 시간은 모델 로딩부터 DB 처리까지 포함하므로 순수 질의 지연 시간으로 해석하면 안 됩니다.
    report = {
        "model": MODEL,
        "revision": REVISION,
        "dimensions": 384,
        "device": "cpu",
        "products": len(rows),
        "elapsed_seconds": time.perf_counter() - start,
        "examples": results,
        "top1_category_matches": sum(r["top1_category_match"] for r in results),
        "queries": len(results),
        "limitation": "직접 작성한 4개 질의에 대한 시연. 별도 평가셋 일반화 성능이나 추천 시스템 품질의 증거가 아님.",
    }
    write_json(REPORTS / "semantic.json", report)
    return report


if __name__ == "__main__":
    import json

    print(json.dumps(run(), ensure_ascii=False, indent=2))
