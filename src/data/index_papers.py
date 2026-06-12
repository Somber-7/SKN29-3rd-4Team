"""
논문 데이터 ChromaDB 인덱싱 스크립트

입력: data/processed/paper_documents.json
출력: ChromaDB 'paper_col' 컬렉션 적재
"""

import os
import json
from pathlib import Path
from tqdm import tqdm
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# --- 설정 ---
ROOT_DIR = Path(__file__).resolve().parents[2]
CHROMA_DB_PATH = ROOT_DIR / "data" / "chroma"
JSON_PATH = ROOT_DIR / "data" / "processed" / "paper_documents.json"
COLLECTION_NAME = "paper_col"
MODEL_NAME = "jhgan/ko-sroberta-multitask"

def main():
    if not JSON_PATH.exists():
        print(f"오류: {JSON_PATH} 파일을 찾을 수 없습니다.")
        return

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        documents = json.load(f)

    if not documents:
        print("경고: 인덱싱할 문서가 없습니다.")
        return

    print(f"총 {len(documents)}건의 논문 청크 데이터를 로드했습니다.")
    
    # 1. 임베딩 모델 로드
    print(f"임베딩 모델 로드 중... ({MODEL_NAME})")
    model = SentenceTransformer(MODEL_NAME)

    # 2. ChromaDB 초기화
    print(f"ChromaDB 초기화 중... ({CHROMA_DB_PATH})")
    client = chromadb.PersistentClient(
        path=str(CHROMA_DB_PATH),
        settings=Settings(anonymized_telemetry=False)
    )

    # 기존 컬렉션이 있다면 가져오거나 새로 생성
    try:
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
    except Exception as e:
        print(f"컬렉션 초기화 오류: {e}")
        return

    # 3. 데이터 준비 (배치 단위 처리 방지용)
    ids = []
    texts = []
    metadatas = []

    for i, item in enumerate(documents):
        # metadata 내의 value는 string, int, float, bool만 허용됨. 
        # page_number가 혹시 None일 경우를 대비
        meta = item["metadata"].copy()
        for k, v in list(meta.items()):
            if v is None:
                meta[k] = ""
            elif not isinstance(v, (str, int, float, bool)):
                meta[k] = str(v)
                
        # id가 명시적으로 없으므로 동적 생성
        doc_id = f"paper_{meta.get('source', 'unknown')}_{meta.get('page_number', '0')}_{i}"
        
        ids.append(doc_id)
        texts.append(item.get("content", ""))
        metadatas.append(meta)

    # 4. 임베딩 및 저장 (100건씩 배치 삽입)
    batch_size = 100
    total_batches = (len(ids) + batch_size - 1) // batch_size
    
    print("인덱싱 시작...")
    for i in tqdm(range(0, len(ids), batch_size), total=total_batches, desc="Indexing"):
        batch_ids = ids[i:i+batch_size]
        batch_texts = texts[i:i+batch_size]
        batch_metas = metadatas[i:i+batch_size]
        
        # 모델 직접 호출하여 임베딩 생성 (리스트 형태 반환)
        embeddings = model.encode(batch_texts, show_progress_bar=False)
        # NumPy array를 List[float] 형태로 변환
        embeddings_list = embeddings.tolist()
        
        collection.upsert(
            ids=batch_ids,
            embeddings=embeddings_list,
            documents=batch_texts,
            metadatas=batch_metas
        )

    print("인덱싱 완료.")
    print(f"현재 '{COLLECTION_NAME}' 컬렉션 총 문서 수: {collection.count()}")

if __name__ == "__main__":
    main()
