"""
rag_server.py — ChromaDB 벡터 검색 MCP 서버 (internal_rag 노드)

전처리된 수리/오행/한자/법령 문서를 ChromaDB에서 의미 검색합니다.
임베딩 모델: jhgan/ko-sroberta-multitask (로컬, sentence-transformers)

[컬렉션 구조]
  suri_col      — 81수리 운세 문서
  ohaeng_col    — 오행 조합 운세 문서
  hanja_col     — 한자 뜻/음/획수 문서
  law_col       — 법령 PDF 파싱 문서
  urimalsam_col — 순우리말 이름 문서

[Tool 목록]
  1. search_rag — 컬렉션 지정 의미 검색
  2. list_collections — 사용 가능한 컬렉션 목록 조회
"""

import os
import re
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
_COLLECTIONS = ["suri_col", "ohaeng_col", "hanja_col", "law_col", "urimalsam_col"]


def _get_collection(name: str):
    """컬렉션을 가져옵니다. 없으면 None 반환."""
    try:
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

    Args:
        query: 검색 질문 (자연어 그대로 입력)
        collection: 검색할 컬렉션 이름 (suri_col / ohaeng_col / hanja_col / law_col / urimalsam_col)
        n_results: 반환할 문서 수 (기본값: 5, 최대: 10)

    Returns:
        유사도 순으로 정렬된 문서 목록 (문서 내용 + 메타데이터 포함)
    """
    if collection not in _COLLECTIONS:
        return (
            f"[오류] '{collection}'은 유효하지 않은 컬렉션입니다.\n"
            f"사용 가능: {', '.join(_COLLECTIONS)}"
        )

    n_results = min(n_results, 10)

    col = _get_collection(collection)
    if col is None:
        return (
            f"[결과 없음] '{collection}' 컬렉션이 존재하지 않습니다.\n"
            f"인덱싱이 완료되지 않았거나 경로가 잘못되었습니다.\n"
            f"ChromaDB 경로: {CHROMA_DIR}"
        )

    where, cond_desc = _parse_hanja_conditions(query) if collection == "hanja_col" else (None, "")

    try:
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
            f"답변 작성 시 각 항목의 [한자: 자원오행표] 태그를 그대로 포함하세요.\n"
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
