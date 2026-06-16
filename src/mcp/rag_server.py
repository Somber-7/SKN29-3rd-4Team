"""
rag_server.py — ChromaDB 벡터 검색 MCP 서버 (internal_rag 노드)

전처리된 수리/오행/한자/법령/논문 문서를 ChromaDB에서 의미 검색합니다.
임베딩 모델: jhgan/ko-sroberta-multitask (로컬, sentence-transformers)

[컬렉션 구조]
  suri_col      — 81수리 운세 문서
  ohaeng_col    — 오행 조합 운세 문서
  hanja_col     — 한자 뜻/음/획수 문서
  law_col       — 법령 PDF 파싱 문서
  urimalsam_col — 순우리말 이름 문서
  paper_col     — 작명 관련 학술 논문 문서 (본문 + 통계표, 266건)

[Tool 목록]
  1. search_rag — 컬렉션 지정 의미 검색
  2. list_collections — 사용 가능한 컬렉션 목록 조회
"""

import os
import re
import random
import functools
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from fastmcp import FastMCP

mcp = FastMCP("RAGServer")

# ─────────────────────────────────────────────
# ChromaDB 클라이언트 및 임베딩 모델 초기화
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DIR = os.path.join(BASE_DIR, "..", "..", "data", "chroma")

_client = chromadb.PersistentClient(path=CHROMA_DIR)

_embedding_fn = SentenceTransformerEmbeddingFunction(
    model_name="jhgan/ko-sroberta-multitask"
)

# 사용 가능한 컬렉션 목록 (인덱싱 완료된 것만 추가)
_COLLECTIONS = ["suri_col", "ohaeng_col", "hanja_col", "law_col", "urimalsam_col", "paper_col"]


def _get_collection(name: str):
    """컬렉션을 가져옵니다. 없으면 None 반환.

    paper_col은 수동 임베딩으로 인덱싱되어 EF 없이 가져옵니다.
    """
    try:
        if name == "paper_col":
            return _client.get_collection(name=name)
        return _client.get_collection(name=name, embedding_function=_embedding_fn)
    except Exception:
        return None


def _parse_hanja_conditions(query: str) -> tuple[dict | None, str]:
    """쿼리에서 획수/오행 조건을 파싱합니다. hanja_col 전용.

    Returns:
        (where_dict, condition_desc)
        조건이 없으면 (None, "")
    """
    conditions = []
    desc_parts = []

    stroke_match = re.search(r'(\d+)\s*획', query)
    if stroke_match:
        n = int(stroke_match.group(1))
        conditions.append({"strokes": n})
        desc_parts.append(f"획수 {n}획")

    _HANJA_TO_OHAENG = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}
    ohaeng_match = re.search(r'([木火土金水목화토금수])오행', query)
    if ohaeng_match:
        raw = ohaeng_match.group(1)
        o = _HANJA_TO_OHAENG.get(raw, raw)   # 한자면 한글로 변환, 이미 한글이면 그대로
        conditions.append({"resource_ohaeng": o})
        desc_parts.append(f"자원오행 {o}")

    if not conditions:
        return None, ""
    where = conditions[0] if len(conditions) == 1 else {"$and": conditions}
    return where, ", ".join(desc_parts)


def _parse_paper_conditions(query: str) -> tuple[dict | None, str]:
    """쿼리에서 chunk_type 조건을 파싱합니다. paper_col 전용.

    Returns:
        (where_dict, condition_desc)
        조건이 없으면 (None, "") → text+table 전체 검색
    """
    _TABLE_KEYWORDS = {"표", "통계", "순위표", "순위", "빈도표", "표 형식"}
    _TEXT_KEYWORDS  = {"본문", "텍스트", "내용만"}

    if any(kw in query for kw in _TABLE_KEYWORDS):
        return {"chunk_type": "table"}, "chunk_type=table"
    if any(kw in query for kw in _TEXT_KEYWORDS):
        return {"chunk_type": "text"}, "chunk_type=text"
    return None, ""


# ═══════════════════════════════════════════════════════
# 인명용 한자 캐시 (서버 시작 시 1회 로드, 이후 메모리에서 즉시 반환)
# ═══════════════════════════════════════════════════════

_HANJA_TO_OHAENG_KR = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}

@functools.lru_cache(maxsize=1)
def _load_person_name_hanja() -> list:
    """is_person_name_hanja=True 한자 전체를 메모리에 캐싱합니다."""
    col = _get_collection("hanja_col")
    if col is None:
        return []
    results = col.get(
        where={"is_person_name_hanja": True},
        include=["metadatas", "documents"],
    )
    return list(zip(results["documents"], results["metadatas"]))


@functools.lru_cache(maxsize=512)
def get_hanja_ohaeng(hanja_char: str) -> str:
    """ChromaDB hanja_col에서 단일 한자의 자원오행을 조회합니다.
    is_person_name_hanja 필터 없이 전체 한자 검색합니다."""
    col = _get_collection("hanja_col")
    if col is None:
        return ""
    try:
        results = col.get(
            where={"hanja": hanja_char},
            include=["metadatas"],
        )
        metas = results.get("metadatas") or []
        if metas:
            return metas[0].get("resource_ohaeng", "") or ""
    except Exception:
        pass
    return ""


def get_hanja_strokes(hanja_char: str) -> int:
    """ChromaDB hanja_col에서 단일 한자의 획수를 조회합니다."""
    col = _get_collection("hanja_col")
    if col is None:
        return 0
    try:
        results = col.get(
            where={"hanja": hanja_char},
            include=["metadatas"],
        )
        metas = results.get("metadatas") or []
        if metas:
            return int(metas[0].get("strokes", 0) or 0)
    except Exception:
        pass
    return 0


@functools.lru_cache(maxsize=1)
def _load_urimalsam() -> list:
    """순우리말 이름 전체를 메모리에 캐싱합니다."""
    col = _get_collection("urimalsam_col")
    if col is None:
        return []
    results = col.get(include=["documents", "metadatas"])
    return list(zip(results["documents"], results["metadatas"]))


_FEMALE_GENDER_KW = {"여자아이", "여아", "딸", "여자", "여자이름"}
_MALE_GENDER_KW   = {"남자아이", "남아", "아들", "남자", "남자이름"}

def sample_urimalsam(query: str, n_results: int = 30, single_only: bool = False) -> str:
    """순우리말 이름을 메모리 캐시에서 랜덤 샘플링합니다.

    이름 추천 전용. 시맨틱 검색 대신 성별 필터 + 랜덤 샘플을 사용해
    매 요청마다 다양한 이름 후보를 제공합니다.
    single_only=True 이면 1음절 단어만 샘플링합니다 (외자 요청 시).
    """
    all_names = _load_urimalsam()

    is_female = any(kw in query for kw in _FEMALE_GENDER_KW)
    is_male   = any(kw in query for kw in _MALE_GENDER_KW)

    if is_female:
        pool = [(doc, meta) for doc, meta in all_names
                if "남자에게" not in (meta or {}).get("gender", "")]
        filter_info = " [성별 필터: 여아 위주]"
    elif is_male:
        pool = [(doc, meta) for doc, meta in all_names
                if "여자에게" not in (meta or {}).get("gender", "")]
        filter_info = " [성별 필터: 남아 위주]"
    else:
        pool = all_names
        filter_info = ""

    if not pool:
        pool = all_names

    if single_only:
        single_pool = [(doc, meta) for doc, meta in pool
                       if len((meta or {}).get("name", doc)) == 1]
        if single_pool:
            pool = single_pool
            filter_info += " [외자 필터: 1음절]"

    sampled = random.sample(pool, min(n_results, len(pool)))

    lines = [
        f"[urimalsam_col 결과] {len(sampled)}건 랜덤 샘플"
        f" (전체 {len(pool)}건 중){filter_info}\n"
        f"아래 목록의 단어만 이름으로 사용하세요. 목록에 없는 단어 절대 금지.\n"
    ]
    for _, meta in sampled:
        m = meta or {}
        lines.append(f"- {m.get('name','')} | 뜻: {m.get('meaning','')}")
    return "\n".join(lines)


def sample_hanja(query: str, n_results: int = 20) -> str:
    """인명용 한자를 메모리 캐시에서 샘플링합니다.

    이름 추천 전용. 시맨틱 검색 대신 메타데이터 필터 + 랜덤 샘플을 사용해
    매 요청마다 다양한 한자 풀을 제공합니다.
    쿼리에 오행(木/火/土/金/水)이 명시된 경우 해당 자원오행으로도 필터링합니다.
    """
    all_hanja = _load_person_name_hanja()

    ohaeng = None
    ohaeng_match = re.search(r'([木火土金水목화토금수])오행', query)
    if ohaeng_match:
        raw = ohaeng_match.group(1)
        ohaeng = _HANJA_TO_OHAENG_KR.get(raw, raw)

    if ohaeng:
        pool = [(doc, meta) for doc, meta in all_hanja
                if (meta or {}).get("resource_ohaeng") == ohaeng]
        filter_info = f" [자원오행 필터: {ohaeng}]"
    else:
        pool = all_hanja
        filter_info = ""

    if not pool:
        return f"[결과 없음] '{ohaeng}' 오행 인명용 한자가 없습니다."

    sampled = random.sample(pool, min(n_results, len(pool)))

    lines = [
        f"[인명용 한자 풀] {len(sampled)}건 샘플"
        f" (전체 {len(pool)}건 중){filter_info}\n"
        f"답변 작성 시 아래 목록의 한자만 사용하세요. 목록에 없는 한자 사용 금지.\n"
        f"각 항목의 hangul 필드가 해당 한자의 실제 독음(이름에서 읽히는 발음)입니다.\n"
    ]
    for i, (_, meta) in enumerate(sampled, 1):
        m = meta or {}
        lines.append(
            f"[{i}] {m.get('hanja','')}({m.get('hangul','')}) | "
            f"획수: {m.get('strokes','?')}획 | "
            f"자원오행: {m.get('resource_ohaeng','?')} | "
            f"발음오행: {m.get('sound_ohaeng','?')} | "
            f"뜻: {m.get('sound_meaning','')}\n"
            f"    [한자: 자원오행표 {m.get('resource_ohaeng','?')}오행]"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# Tool 1: 의미 검색
# ═══════════════════════════════════════════════════════

@mcp.tool()
def search_rag(query: str, collection: str, n_results: int = 5) -> str:
    """
    ChromaDB 컬렉션에서 질문과 의미적으로 유사한 문서를 검색합니다.

    호출 조건:
      - internal_rag 노드에서 LLM 답변 생성 전 참고 문서 수집 시
      - 수리/오행/한자/법령 관련 질문에 대한 내부 지식 검색 시

    컬렉션 선택 기준:
      - suri_col      : 수리, 4격, 운세, 초년운/청년운/중년운/총운 관련 질문
      - ohaeng_col    : 오행, 상생, 상극, 木火土金水 조합 관련 질문
      - hanja_col     : 한자 뜻, 획수, 음(독음), 추천 관련 질문
      - law_col       : 법령, 인명용 한자, 출생신고, 작명 규정 관련 질문
      - urimalsam_col : 순우리말 이름, 이름 뜻, 성별 경향, 최근 추세 관련 질문
      - paper_col     : 작명 관련 학술 논문, 이름 트렌드/유행 연구, 명명 패턴 통계 관련 질문
                        쿼리에 "표"/"통계"/"순위" 포함 시 → 통계표 청크 우선 검색

    Args:
        query: 검색 질문 (자연어 그대로 입력)
        collection: 검색할 컬렉션 이름 (suri_col / ohaeng_col / hanja_col / law_col / urimalsam_col / paper_col)
        n_results: 반환할 문서 수 (기본값: 5, 최대: 10)

    Returns:
        유사도 순으로 정렬된 문서 목록 (문서 내용 + 메타데이터 포함)
    """
    if collection not in _COLLECTIONS:
        return (
            f"[오류] '{collection}'은 유효하지 않은 컬렉션입니다.\n"
            f"사용 가능: {', '.join(_COLLECTIONS)}"
        )

    n_results = min(n_results, 30)

    col = _get_collection(collection)
    if col is None:
        return (
            f"[결과 없음] '{collection}' 컬렉션이 존재하지 않습니다.\n"
            f"인덱싱이 완료되지 않았거나 경로가 잘못되었습니다.\n"
            f"ChromaDB 경로: {CHROMA_DIR}"
        )

    if collection == "hanja_col":
        where, cond_desc = _parse_hanja_conditions(query)
    elif collection == "paper_col":
        where, cond_desc = _parse_paper_conditions(query)
    else:
        where, cond_desc = None, ""

    try:
        # paper_col은 수동 임베딩으로 인덱싱 → 쿼리도 동일 모델로 수동 임베딩
        if collection == "paper_col":
            query_embedding = _embedding_fn([query])
            results = col.query(
                query_embeddings=query_embedding,
                n_results=n_results,
                **({"where": where} if where else {}),
            )
        else:
            results = col.query(
                query_texts=[query],
                n_results=n_results,
                **({"where": where} if where else {}),
            )
    except Exception as e:
        return f"[오류] 검색 중 오류 발생: {str(e)}"

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not documents:
        return f"[결과 없음] '{query}'에 대한 유사 문서를 찾지 못했습니다."

    if collection == "hanja_col":
        filter_info = f" [조건 필터: {cond_desc}]" if cond_desc else ""
        lines = [
            f"[검색 결과] '{query}' — hanja_col ({len(documents)}건){filter_info}\n"
        ]
        for i, meta in enumerate(metadatas, 1):
            m = meta or {}
            hanja       = m.get("hanja", "")
            hangul      = m.get("hangul", "")
            strokes     = m.get("strokes", "?")
            res_ohaeng  = m.get("resource_ohaeng", "?")
            snd_ohaeng  = m.get("sound_ohaeng", "?")
            meaning     = m.get("sound_meaning", "")
            is_person   = "예" if m.get("is_person_name_hanja") else "아니오"
            lines.append(
                f"[{i}] {hanja}({hangul}) | 획수: {strokes}획 | "
                f"자원오행: {res_ohaeng} | 발음오행: {snd_ohaeng} | "
                f"뜻: {meaning} | 인명용: {is_person}\n"
                f"    [한자: 자원오행표 {res_ohaeng}오행]"
            )
    elif collection == "paper_col":
        filter_info = f" [필터: {cond_desc}]" if cond_desc else ""
        lines = [
            f"[paper_col] '{query}' 검색 결과 {len(documents)}건{filter_info}\n"
        ]
        for i, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances), 1):
            m = meta or {}
            similarity  = round(1 - dist, 4)
            title       = m.get("title", "")
            author      = m.get("author", "")
            year        = m.get("year", "")
            page        = m.get("page_number", "?")
            chunk_type  = m.get("chunk_type", "text")
            limit       = 500 if chunk_type == "table" else 300
            content     = doc[:limit] + ("..." if len(doc) > limit else "")
            lines.append(
                f"  [{i}] 유사도: {similarity} | {chunk_type}\n"
                f"      {title}({year}) — {author} | p.{page}\n"
                f"      {content}"
            )
    else:
        lines = [f"[{collection}] '{query}' 검색 결과 {len(documents)}건\n"]
        for i, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances), 1):
            similarity = round(1 - dist, 4)
            meta_str = " | ".join(
                f"{k}: {v}" for k, v in (meta or {}).items()
                if k not in {"type", "collection", "source"}
            )
            lines.append(
                f"  [{i}] 유사도: {similarity}\n"
                f"      메타: {meta_str}\n"
                f"      내용: {doc[:200]}{'...' if len(doc) > 200 else ''}\n"
                f"      [출처: {collection}]"
            )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# Tool 2: 컬렉션 목록 조회
# ═══════════════════════════════════════════════════════

@mcp.tool()
def list_collections() -> str:
    """
    현재 ChromaDB에 존재하는 컬렉션 목록과 각 컬렉션의 문서 수를 반환합니다.

    호출 조건:
      - 어떤 컬렉션을 사용할 수 있는지 확인할 때
      - 인덱싱 완료 여부를 점검할 때

    Returns:
        컬렉션 이름과 문서 수 목록
    """
    lines = ["[ChromaDB 컬렉션 현황]\n"]

    for name in _COLLECTIONS:
        col = _get_collection(name)
        if col is None:
            lines.append(f"  - {name}: 미생성 (인덱싱 필요)")
        else:
            count = col.count()
            lines.append(f"  - {name}: {count}건")

    lines.append(f"\nChromaDB 경로: {CHROMA_DIR}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 서버 실행
# ─────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
