import os
import json
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from tqdm import tqdm

def main():
    # 경로 설정
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    CHROMA_DIR = os.path.join(BASE_DIR, "..", "..", "data", "chroma")
    PROCESSED_DIR = os.path.join(BASE_DIR, "..", "..", "data", "processed")

    suri_json_path = os.path.join(PROCESSED_DIR, "suri_documents.json")
    ohaeng_json_path = os.path.join(PROCESSED_DIR, "ohaeng_documents.json")

    # ChromaDB 클라이언트 및 임베딩 함수 초기화
    print(f"Initializing ChromaDB persistent client at {CHROMA_DIR}...")
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    
    print("Loading embedding model (jhgan/ko-sroberta-multitask)...")
    embedding_fn = SentenceTransformerEmbeddingFunction(model_name="jhgan/ko-sroberta-multitask")

    # 1. 81수리 데이터 인덱싱
    print(f"Loading {suri_json_path}...")
    with open(suri_json_path, "r", encoding="utf-8") as f:
        suri_data = json.load(f)

    # 기존 컬렉션이 있다면 삭제하고 새로 생성 (초기화)
    try:
        client.delete_collection("suri_col")
        print("Deleted existing 'suri_col' collection.")
    except Exception:
        pass

    suri_col = client.create_collection(
        name="suri_col", 
        embedding_function=embedding_fn,
        metadata={"description": "81수리 획수 운세"}
    )

    ids = [item["id"] for item in suri_data]
    documents = [item["document"] for item in suri_data]
    metadatas = [item["metadata"] for item in suri_data]

    # 배치 단위로 추가 (ChromaDB의 배치 제한 고려, 보통 100~5461)
    batch_size = 100
    print(f"Indexing 'suri_col' ({len(ids)} documents)...")
    for i in tqdm(range(0, len(ids), batch_size)):
        suri_col.add(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size],
            metadatas=metadatas[i:i+batch_size]
        )
    print("suri_col indexing completed.\n")

    # 2. 오행 조합 운세 데이터 인덱싱
    print(f"Loading {ohaeng_json_path}...")
    with open(ohaeng_json_path, "r", encoding="utf-8") as f:
        ohaeng_data = json.load(f)

    try:
        client.delete_collection("ohaeng_col")
        print("Deleted existing 'ohaeng_col' collection.")
    except Exception:
        pass

    ohaeng_col = client.create_collection(
        name="ohaeng_col", 
        embedding_function=embedding_fn,
        metadata={"description": "오행 조합 운세"}
    )

    ids = [item["id"] for item in ohaeng_data]
    documents = [item["document"] for item in ohaeng_data]
    metadatas = [item["metadata"] for item in ohaeng_data]

    print(f"Indexing 'ohaeng_col' ({len(ids)} documents)...")
    for i in tqdm(range(0, len(ids), batch_size)):
        ohaeng_col.add(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size],
            metadatas=metadatas[i:i+batch_size]
        )
    print("ohaeng_col indexing completed.\n")

    print("All done!")

if __name__ == "__main__":
    main()
