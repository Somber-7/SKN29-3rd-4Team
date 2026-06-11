# -*- coding: utf-8 -*-
"""
preprocess_law.py — 법령 PDF 파싱 및 KoNLPy 전처리

[처리 순서]
  1. PDF 텍스트 추출 (pypdf)
  2. 법제처 페이지 헤더 제거
  3. 조항(Article) 단위 분할
  4. KoNLPy Okt 형태소 분석 → 불용어 제거 → 텍스트 정규화
  5. data/processed/law_articles.json 저장

출력 파일: data/processed/law_articles.json
다음 단계: python src/data/index_law.py

실행 방법: python src/data/preprocess_law.py
"""

import os
import re
import sys
import glob
import json

import pypdf
from konlpy.tag import Okt

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PDF_DIR = os.path.join(BASE_DIR, "data", "raw", "pdf")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "processed", "law_articles.json")

LAW_KEYWORDS = {
    "법률": "관한 법률",
    "규칙": "관한 규칙",
}

# 법령 텍스트 불용어 — 조사/어미/접속사 등 의미 없는 토큰
_STOPWORDS = {
    "이", "그", "저", "것", "수", "등", "및", "또", "또는", "즉",
    "의", "을", "를", "이", "가", "은", "는", "에", "에서", "로", "으로",
    "와", "과", "도", "만", "도", "까지", "부터", "에게", "한", "하여",
    "따라", "관한", "대한", "위한", "의한", "경우", "때", "중", "후",
    "전", "내", "외", "간", "각", "같은", "다른", "해당", "관련",
}


# ─────────────────────────────────────────────
# 1단계: PDF 텍스트 추출
# ─────────────────────────────────────────────

def find_pdf(keyword: str) -> str | None:
    for f in glob.glob(os.path.join(PDF_DIR, "*.pdf")):
        if keyword in f:
            return f
    return None


def extract_text(pdf_path: str) -> str:
    reader = pypdf.PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n".join(pages)


# ─────────────────────────────────────────────
# 2단계: 헤더 제거 + 조항 분할
# ─────────────────────────────────────────────

def clean_header(text: str, law_name: str) -> str:
    pattern = rf"법제처\s+\d+\s+국가법령정보센터\n{re.escape(law_name)}\n"
    return re.sub(pattern, "", text)


def parse_articles(text: str, source: str) -> list[dict]:
    pattern = re.compile(r"(제\d+조의?\d*\([^)]+\))")
    matches = list(pattern.finditer(text))

    articles = []
    for i, match in enumerate(matches):
        article_header = match.group(1)

        num_match = re.search(r"제(\d+조의?\d*)", article_header)
        article_num = num_match.group(1) if num_match else str(i + 1)

        title_match = re.search(r"\(([^)]+)\)", article_header)
        title = title_match.group(1) if title_match else ""

        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        if len(body) < 20:
            continue

        articles.append({
            "source": source,
            "article_num": article_num,
            "title": title,
            "raw_text": body,
        })

    return articles


# ─────────────────────────────────────────────
# 3단계: KoNLPy 전처리
# ─────────────────────────────────────────────

_okt = Okt()


def normalize_text(text: str) -> str:
    """특수문자 정규화 — PDF 파싱 노이즈 제거."""
    # 줄 끊김 복원: 줄 끝 하이픈 또는 단어 중간 개행
    text = re.sub(r"-\n", "", text)
    text = re.sub(r"(?<=[가-힣])\n(?=[가-힣])", "", text)
    # 연속 공백 정리
    text = re.sub(r"[ \t]{2,}", " ", text)
    # 특수 구분자 정리 (・ → ,)
    text = text.replace("・", ", ")
    return text.strip()


def remove_stopwords(tokens: list[tuple[str, str]]) -> list[str]:
    """
    품사 태그 기반 불용어 제거.
    유지: Noun(명사), Number(수사), Alpha(영문)
    제거: Josa(조사), Eomi(어미), Punctuation(구두점) + 불용어 사전
    """
    kept = []
    for word, pos in tokens:
        if pos in ("Josa", "Eomi", "Punctuation", "Foreign"):
            continue
        if word in _STOPWORDS:
            continue
        if len(word) < 2 and pos not in ("Number", "Alpha"):
            continue
        kept.append(word)
    return kept


def preprocess_article(article: dict) -> dict:
    """
    단일 조항에 KoNLPy 전처리 적용.
    - normalized_text: 정규화된 원문 (ChromaDB 저장용)
    - tokens: 불용어 제거 후 핵심 토큰 목록 (검색 품질 보조)
    """
    raw = article["raw_text"]

    # 정규화
    normalized = normalize_text(raw)

    # 형태소 분석
    pos_tags = _okt.pos(normalized, norm=True, stem=True)

    # 불용어 제거
    tokens = remove_stopwords(pos_tags)

    return {
        **article,
        "normalized_text": normalized,
        "tokens": tokens,
    }


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    print("KoNLPy Okt 초기화 중...")
    # _okt는 모듈 로드 시 이미 초기화됨

    all_articles = []

    for source, keyword in LAW_KEYWORDS.items():
        pdf_path = find_pdf(keyword)
        if not pdf_path:
            print(f"[스킵] '{keyword}' 포함 PDF 없음")
            continue

        print(f"\n[{source}] 파싱 중: {os.path.basename(pdf_path)}")
        text = extract_text(pdf_path)
        text = clean_header(text, f"가족관계의 등록 등에 관한 {source}")
        articles = parse_articles(text, source)
        print(f"  조항 수: {len(articles)}")

        print(f"  KoNLPy 전처리 중...")
        for article in articles:
            processed = preprocess_article(article)
            all_articles.append(processed)

        # 샘플 출력
        if articles:
            sample = preprocess_article(articles[0])
            print(f"  샘플 [{sample['article_num']}({sample['title']})]")
            print(f"    정규화: {sample['normalized_text'][:60]}...")
            print(f"    토큰:   {sample['tokens'][:10]}")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_articles, f, ensure_ascii=False, indent=2)

    print(f"\n총 {len(all_articles)}개 조항 → {OUTPUT_PATH} 저장 완료")


if __name__ == "__main__":
    main()
