import os
import sys
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# Windows 콘솔 인코딩 문제 방지
sys.stdout.reconfigure(encoding='utf-8')

def main():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    CHROMA_DIR = os.path.join(BASE_DIR, "..", "..", "data", "chroma")

    print(f"Connecting to ChromaDB at: {CHROMA_DIR}")
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    embedding_fn = SentenceTransformerEmbeddingFunction(model_name="jhgan/ko-sroberta-multitask")

    # 1. 수리 데이터 검색 테스트
    try:
        suri_col = client.get_collection("suri_col", embedding_function=embedding_fn)
        query_suri = "큰 성공과 부귀영화를 누리는 대길수 추천해줘"
        print(f"\n======================================")
        print(f"[수리 데이터 검색 테스트]")
        print(f"질문: '{query_suri}'")
        print(f"======================================")
        
        results = suri_col.query(query_texts=[query_suri], n_results=3)
        for i, (doc, meta, dist) in enumerate(zip(results['documents'][0], results['metadatas'][0], results['distances'][0])):
            similarity = 1 - dist
            print(f"[{i+1}] 수리: {meta.get('suri_num')}수 ({meta.get('gyeok_ko')}) | 길흉: {meta.get('gilhyung')} | 유사도: {similarity:.4f}")
            print(f"내용: {doc[:150]}...\n")
    except Exception as e:
        print(f"수리 컬렉션 검색 오류: {e}")

    # 2. 오행 데이터 검색 테스트
    try:
        ohaeng_col = client.get_collection("ohaeng_col", embedding_function=embedding_fn)
        query_ohaeng = "부부 사이가 화목하고 건강하게 장수하는 오행 조합"
        print(f"======================================")
        print(f"[오행 데이터 검색 테스트]")
        print(f"질문: '{query_ohaeng}'")
        print(f"======================================")
        
        results = ohaeng_col.query(query_texts=[query_ohaeng], n_results=3)
        for i, (doc, meta, dist) in enumerate(zip(results['documents'][0], results['metadatas'][0], results['distances'][0])):
            similarity = 1 - dist
            print(f"[{i+1}] 조합: {meta.get('combo')} | 흐름: {meta.get('flow')} | 유사도: {similarity:.4f}")
            print(f"내용: {doc[:150]}...\n")
    except Exception as e:
        print(f"오행 컬렉션 검색 오류: {e}")

if __name__ == "__main__":
    main()
