# -*- coding: utf-8 -*-
"""
index_urimalsam.py — 순우리말 이름 ChromaDB urimalsam_col 인덱싱

사전 조건: python src/data/crawl_urimalsam.py 실행 완료
입력 파일: data/processed/urimalsam_names.json
출력:      ChromaDB urimalsam_col 컬렉션

실행 방법: python src/data/index_urimalsam.py
"""

import os
import sys
import json
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INPUT_PATH = os.path.join(BASE_DIR, "data", "processed", "urimalsam_names.json")
CHROMA_DIR = os.path.join(BASE_DIR, "data", "chroma")

# ─────────────────────────────────────────────
# ChromaDB 초기화
# ─────────────────────────────────────────────

_client = chromadb.PersistentClient(path=CHROMA_DIR)
_embedding_fn = SentenceTransformerEmbeddingFunction(
    model_name="jhgan/ko-sroberta-multitask"
)

BATCH = 500


# ─────────────────────────────────────────────
# 인덱싱
# ─────────────────────────────────────────────

def build_records(names: list[dict]) -> list[dict]:
    records = []
    seen = set()

    for item in names:
        name = item["name"]
        if name in seen:
            continue
        seen.add(name)

        document = (
            f"[순우리말 이름] {name}\n"
            f"뜻: {item['meaning']}\n"
            f"성별 경향: {item['gender']}\n"
            f"최근 추세: {item['trend']}"
        )

        records.append({
            "id": f"urimalsam_{name}",
            "document": document,
            "metadata": {
                "name": name,
                "meaning": item["meaning"],
                "gender": item["gender"],
                "trend": item["trend"],
                "type": "urimalsam",
            },
        })

    return records


def index_to_chroma(records: list[dict]) -> None:
    if not records:
        print("  인덱싱할 데이터 없음")
        return

    col = _client.get_or_create_collection(
        name="urimalsam_col",
        embedding_function=_embedding_fn,
    )

    existing = set(col.get(ids=[r["id"] for r in records])["ids"])
    new_records = [r for r in records if r["id"] not in existing]

    if not new_records:
        print("  이미 모두 인덱싱됨, 스킵")
        return

    for i in range(0, len(new_records), BATCH):
        batch = new_records[i:i + BATCH]
        col.add(
            ids=[r["id"] for r in batch],
            documents=[r["document"] for r in batch],
            metadatas=[r["metadata"] for r in batch],
        )
        print(f"  {i + len(batch)}/{len(new_records)}건 삽입 완료")


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    if not os.path.exists(INPUT_PATH):
        print(f"[오류] {INPUT_PATH} 없음")
        print("먼저 실행하세요: python src/data/crawl_urimalsam.py")
        return

    print(f"데이터 로드: {INPUT_PATH}")
    with open(INPUT_PATH, encoding="utf-8") as f:
        names = json.load(f)

    print(f"이름 수: {len(names)}")

    records = build_records(names)
    print(f"ChromaDB 레코드: {len(records)}개 → urimalsam_col 인덱싱 시작")

    index_to_chroma(records)
    print("\n완료")


if __name__ == "__main__":
    main()
