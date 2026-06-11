"""
index_law.py — 법령 PDF 파싱 및 ChromaDB law_col 인덱싱

대상 파일:
  - 가족관계의 등록 등에 관한 법률 (22페이지)
  - 가족관계의 등록 등에 관한 규칙 (25페이지)

청킹 전략: 조항(Article) 단위 — 제N조(제목) 본문 텍스트
실행 방법: python src/data/index_law.py
"""

import os
import re
import glob
import pypdf
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PDF_DIR = os.path.join(BASE_DIR, "data", "raw", "pdf")
CHROMA_DIR = os.path.join(BASE_DIR, "data", "chroma")

def _find_pdf(keyword: str) -> str | None:
    """PDF_DIR에서 키워드가 포함된 PDF 파일 경로를 반환."""
    for f in glob.glob(os.path.join(PDF_DIR, "*.pdf")):
        if keyword in f:
            return f
    return None

LAW_KEYWORDS = {
    "법률": "관한 법률",
    "규칙": "관한 규칙",
}

# ─────────────────────────────────────────────
# ChromaDB 초기화
# ─────────────────────────────────────────────

_client = chromadb.PersistentClient(path=CHROMA_DIR)
_embedding_fn = SentenceTransformerEmbeddingFunction(
    model_name="jhgan/ko-sroberta-multitask"
)

BATCH = 500


# ─────────────────────────────────────────────
# PDF 파싱
# ─────────────────────────────────────────────

def extract_text(pdf_path: str) -> str:
    """PDF 전체 텍스트 추출."""
    reader = pypdf.PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n".join(pages)


def clean_header(text: str, law_name: str) -> str:
    """법제처 페이지 헤더 제거."""
    pattern = rf"법제처\s+\d+\s+국가법령정보센터\n{re.escape(law_name)}\n"
    return re.sub(pattern, "", text)


def parse_articles(text: str, source: str) -> list[dict]:
    """
    조항 단위로 분할.
    반환: [{"id": ..., "document": ..., "metadata": {...}}, ...]
    """
    # 조항 시작 위치 탐색: 제N조 또는 제N조의M
    pattern = re.compile(r"(제\d+조의?\d*\([^)]+\))")
    matches = list(pattern.finditer(text))

    articles = []
    for i, match in enumerate(matches):
        article_header = match.group(1)

        # 조항 번호 추출 (예: "제44조의2" → "44의2")
        num_match = re.search(r"제(\d+조의?\d*)", article_header)
        article_num = num_match.group(1) if num_match else str(i + 1)

        # 제목 추출 (예: "(목적)")
        title_match = re.search(r"\(([^)]+)\)", article_header)
        title = title_match.group(1) if title_match else ""

        # 본문: 현재 조항 시작 ~ 다음 조항 시작
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        # 너무 짧은 조항 (헤더만 있는 경우) 제외
        if len(body) < 20:
            continue

        doc_id = f"law_{source}_{article_num}"
        document = f"[{source}] {article_header}\n{body}"

        articles.append({
            "id": doc_id,
            "document": document,
            "metadata": {
                "source": source,
                "article_num": article_num,
                "title": title,
                "type": "law",
            },
        })

    return articles


# ─────────────────────────────────────────────
# ChromaDB 인덱싱
# ─────────────────────────────────────────────

def index_to_chroma(articles: list[dict]) -> None:
    """law_col 컬렉션에 500건 단위 배치 삽입."""
    if not articles:
        print("  인덱싱할 조항 없음")
        return

    col = _client.get_or_create_collection(
        name="law_col",
        embedding_function=_embedding_fn,
    )

    # 중복 ID 제거 (같은 배치 내)
    seen = {}
    for a in articles:
        seen[a["id"]] = a
    articles = list(seen.values())

    # 기존 ID 확인 (이미 인덱싱된 것 제외)
    existing = set(col.get(ids=[a["id"] for a in articles])["ids"])
    new_articles = [a for a in articles if a["id"] not in existing]

    if not new_articles:
        print("  이미 모두 인덱싱됨, 스킵")
        return

    for i in range(0, len(new_articles), BATCH):
        batch = new_articles[i:i + BATCH]
        col.add(
            ids=[a["id"] for a in batch],
            documents=[a["document"] for a in batch],
            metadatas=[a["metadata"] for a in batch],
        )
        print(f"  {i + len(batch)}/{len(new_articles)}건 삽입 완료")


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    all_articles = []

    for source, keyword in LAW_KEYWORDS.items():
        pdf_path = _find_pdf(keyword)
        if not pdf_path:
            print(f"[스킵] '{keyword}' 포함 PDF 없음")
            continue

        print(f"\n[{source}] 파싱 중: {os.path.basename(pdf_path)}")
        text = extract_text(pdf_path)
        text = clean_header(text, f"가족관계의 등록 등에 관한 {source}")
        articles = parse_articles(text, source)
        print(f"  조항 수: {len(articles)}")

        all_articles.extend(articles)

    print(f"\n총 {len(all_articles)}개 조항 → law_col 인덱싱 시작")
    index_to_chroma(all_articles)
    print("\n완료")


if __name__ == "__main__":
    main()
