"""
naming_graph.py — 작명 QA LangGraph ReAct StateGraph

[구조]
  LLM Router가 필요한 Tool을 판단 → Tool 실행 → 결과를 LLM에 전달
  → 추가 Tool 필요 여부 재판단 (루프) → 충분하면 최종 답변 생성

[노드 구성]
  llm_router   — LLM이 다음 실행할 Tool 결정 (또는 답변 생성 판단)
  internal_rag — ChromaDB 벡터 검색 (한자/수리/오행/법령/순우리말/논문)
  graph_db     — Neo4j 한자 관계 그래프 조회
  sql_db       — 81수리 계산 / 吉수 역산 / 오행 조합 조회
  external_api — 국가법령정보 / 우리말샘 API 호출
  generate     — 수집된 context로 최종 답변 생성

[Tool 선택 기준 — LLM 판단]
  internal_rag : 한자 뜻/추천, 수리 설명, 오행 설명, 법령 조문, 순우리말 이름 검색
  sql_db       : 획수 수치 계산, 81수리 4격, 吉수 조합 역산, 오행 조합 운세
  external_api : 법령 실시간 조회, 순우리말 단어 존재 여부 검증
  graph_db     : 한자-오행 관계 탐색, 상생/상극 경로 탐색 (Neo4j)
  generate     : 충분한 정보가 수집됐을 때 최종 답변 생성
"""

from __future__ import annotations

import re
import sys
import os
import json

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "mcp"))

from typing import TypedDict, Literal
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
import rag_server
import db_server
import law_server
import graph_server


# ─────────────────────────────────────────────
# State 정의
# ─────────────────────────────────────────────

NextAction = Literal["internal_rag", "graph_db", "sql_db", "external_api", "generate", "clarify", "llm_router"]

MAX_ITERATIONS = 5
CONTEXT_MAX_CHARS = 8000
VALID_TOOLS = {"internal_rag", "graph_db", "sql_db", "external_api", "generate", "clarify"}
_VALID_COLLECTIONS = {"suri_col", "ohaeng_col", "hanja_col", "law_col", "urimalsam_col", "paper_col"}

# clarify_node에서 누락 항목 판별용
_GENDER_KW = {"아들", "딸", "남아", "여아", "남자아이", "여자아이", "남자 아이", "여자 아이",
              "남자이름", "여자이름", "남자 이름", "여자 이름", "남자", "여자", "남녀"}
_TYPE_KW = {"한자", "순우리말", "우리말이름", "우리말 이름", "한글이름", "한글 이름"}
_SINGLE_KW = {"외자", "한 글자", "1글자", "외자이름", "한글자"}

# 상위 50개 성씨 한자 사전 (자원오행 기준)
_SURNAME_DICT: dict[str, dict] = {
    "김": {"hanja": "金", "resource_ohaeng": "금", "strokes": 8},
    "이": {"hanja": "李", "resource_ohaeng": "목", "strokes": 7},
    "박": {"hanja": "朴", "resource_ohaeng": "목", "strokes": 6},
    "최": {"hanja": "崔", "resource_ohaeng": "토", "strokes": 11},
    "정": {"hanja": "鄭", "resource_ohaeng": "토", "strokes": 15},
    "강": {"hanja": "姜", "resource_ohaeng": "토", "strokes": 9},
    "조": {"hanja": "趙", "resource_ohaeng": "토", "strokes": 14},
    "윤": {"hanja": "尹", "resource_ohaeng": "수", "strokes": 4},
    "장": {"hanja": "張", "resource_ohaeng": "목", "strokes": 11},
    "임": {"hanja": "林", "resource_ohaeng": "목", "strokes": 8},
    "한": {"hanja": "韓", "resource_ohaeng": "화", "strokes": 17},
    "오": {"hanja": "吳", "resource_ohaeng": "화", "strokes": 7},
    "서": {"hanja": "徐", "resource_ohaeng": "화", "strokes": 10},
    "신": {"hanja": "申", "resource_ohaeng": "금", "strokes": 5},
    "권": {"hanja": "權", "resource_ohaeng": "목", "strokes": 22},
    "황": {"hanja": "黃", "resource_ohaeng": "토", "strokes": 12},
    "안": {"hanja": "安", "resource_ohaeng": "토", "strokes": 6},
    "송": {"hanja": "宋", "resource_ohaeng": "목", "strokes": 7},
    "류": {"hanja": "柳", "resource_ohaeng": "목", "strokes": 9},
    "유": {"hanja": "劉", "resource_ohaeng": "금", "strokes": 15},
    "홍": {"hanja": "洪", "resource_ohaeng": "수", "strokes": 9},
    "전": {"hanja": "全", "resource_ohaeng": "금", "strokes": 6},
    "고": {"hanja": "高", "resource_ohaeng": "화", "strokes": 10},
    "문": {"hanja": "文", "resource_ohaeng": "화", "strokes": 4},
    "손": {"hanja": "孫", "resource_ohaeng": "금", "strokes": 10},
    "양": {"hanja": "楊", "resource_ohaeng": "목", "strokes": 13},
    "배": {"hanja": "裵", "resource_ohaeng": "화", "strokes": 14},
    "백": {"hanja": "白", "resource_ohaeng": "금", "strokes": 5},
    "허": {"hanja": "許", "resource_ohaeng": "화", "strokes": 11},
    "남": {"hanja": "南", "resource_ohaeng": "화", "strokes": 9},
    "심": {"hanja": "沈", "resource_ohaeng": "수", "strokes": 7},
    "노": {"hanja": "盧", "resource_ohaeng": "화", "strokes": 16},
    "하": {"hanja": "河", "resource_ohaeng": "수", "strokes": 8},
    "곽": {"hanja": "郭", "resource_ohaeng": "토", "strokes": 15},
    "성": {"hanja": "成", "resource_ohaeng": "금", "strokes": 7},
    "차": {"hanja": "車", "resource_ohaeng": "화", "strokes": 7},
    "민": {"hanja": "閔", "resource_ohaeng": "수", "strokes": 12},
    "엄": {"hanja": "嚴", "resource_ohaeng": "화", "strokes": 20},
    "채": {"hanja": "蔡", "resource_ohaeng": "목", "strokes": 17},
    "원": {"hanja": "元", "resource_ohaeng": "토", "strokes": 4},
    "구": {"hanja": "具", "resource_ohaeng": "금", "strokes": 8},
    "우": {"hanja": "禹", "resource_ohaeng": "토", "strokes": 9},
    "도": {"hanja": "都", "resource_ohaeng": "토", "strokes": 12},
    "나": {"hanja": "羅", "resource_ohaeng": "화", "strokes": 19},
    "변": {"hanja": "卞", "resource_ohaeng": "화", "strokes": 4},
    "공": {"hanja": "孔", "resource_ohaeng": "수", "strokes": 4},
    "방": {"hanja": "方", "resource_ohaeng": "화", "strokes": 4},
    "마": {"hanja": "馬", "resource_ohaeng": "화", "strokes": 10},
    "탁": {"hanja": "卓", "resource_ohaeng": "화", "strokes": 8},
    "국": {"hanja": "鞠", "resource_ohaeng": "목", "strokes": 17},
}


def _resolve_surname(query: str) -> tuple[str, dict | None]:
    """쿼리에서 성씨를 추출하고 한자 정보를 반환합니다.
    Returns: (surname_korean, info_dict or None)
    info_dict이 None이면 사전에 없는 성씨 (반문 필요)
    """
    # 1. 괄호 안 한자 직접 제공: "상씨(常)" 또는 "상(常)씨"
    m = re.search(r'([가-힣]{1,2})씨\s*[（\(]([一-鿿]{1,2})[）\)]', query)
    if not m:
        m = re.search(r'([가-힣]{1,2})\s*[（\(]([一-鿿]{1,2})[）\)]\s*씨', query)
    if m:
        surname_kr = m.group(1)
        hanja = m.group(2)
        # 사전에서 오행 찾기 시도, 없으면 미상 처리
        info = _SURNAME_DICT.get(surname_kr)
        if info and info["hanja"] == hanja:
            return surname_kr, info
        return surname_kr, {"hanja": hanja, "resource_ohaeng": "", "strokes": 0}

    # 2. 사전 조회: "김씨"
    m = re.search(r'([가-힣]{1,2})씨', query)
    if not m:
        return "", None
    surname_kr = m.group(1)
    return surname_kr, _SURNAME_DICT.get(surname_kr)


class NamingState(TypedDict):
    query: str              # 사용자 원본 질문
    context: str            # 누적된 Tool 실행 결과
    next_action: NextAction # LLM이 결정한 다음 액션
    answer: str             # 최종 답변
    iterations: int         # 현재 반복 횟수
    used_tools: list[str]   # 이미 실행한 Tool 이력 (중복 방지)
    collections: list[str]  # LLM이 선택한 RAG 컬렉션 목록
    name_length: int        # 이름 글자 수 (1=외자, 2=두글자, 기본값 2)
    surname_hanja: str      # 성씨 한자 (사전 조회 또는 사용자 입력)


# ─────────────────────────────────────────────
# LLM 초기화
# ─────────────────────────────────────────────

# Router: JSON 한 줄 출력 전용
_llm_router = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_tokens=512,
)

# Generate: 이름 추천 최종 답변 생성
_llm_generate = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    max_tokens=1536,
)

# ─────────────────────────────────────────────
# LLM Router 노드
# ─────────────────────────────────────────────

_ROUTER_SYSTEM = """당신은 작명 QA 시스템의 라우터입니다.
사용자 질문과 지금까지 수집된 정보를 보고 다음에 실행할 Tool을 JSON으로 결정하세요.

[Tool 목록]
- internal_rag : ChromaDB 검색 — collections 배열로 검색할 컬렉션을 반드시 지정
  · hanja_col     : 한자 뜻/획수/오행 정보 (한자 이름 추천·조회 시 필수)
  · paper_col     : 작명 트렌드·음절 선호도·성별 통계 논문 (이름 추천 시 hanja_col과 함께)
  · suri_col      : 81수리 운세 설명 (수리 관련 질문)
  · ohaeng_col    : 오행 조합 운세 설명 (오행 관련 질문)
  · law_col       : 가족관계등록법 조문 (법령 관련 질문)
  · urimalsam_col : 순우리말 이름 목록 (순우리말 이름 추천)
- sql_db       : 수치 계산 (81수리 4격, 吉수 역산, 오행 조합 운세)
- external_api : 외부 API (법령 실시간 조회, 순우리말 단어 검증)
- graph_db     : Neo4j 한자 관계 탐색 (상생/상극, 인명용 허용 여부)
- generate     : 수집된 정보로 최종 답변 생성
- clarify      : 이름 추천 요청인데 아래 조건 중 하나라도 해당할 때 사용
  ※ clarify 허용 조건 (아래 중 하나 이상):
    - 성별을 전혀 알 수 없음 (아들/딸/남아/여아/남자/여자/남녀/남자이름/여자이름 미포함)
    - AND/OR 이름 유형(한자/순우리말)도 전혀 알 수 없음
    - AND/OR 성씨가 '○씨' 형태로 언급됐지만 한자가 괄호 안에 없고 흔한 성씨(김이박최정강조윤장임한오서신권황안송류유홍전고문손양배백허남심노하)가 아닌 경우
  ※ 성별·유형 중 하나라도 명시되어 있으면 clarify 금지 → internal_rag로 진행
  ※ "남녀 N개씩", "남자 N개 여자 N개" 등은 성별 양쪽 모두 지정한 것 → clarify 금지
  ※ 이름 유형 미지정 시 한자 이름으로 간주하고 hanja_col + paper_col 검색
  ※ 흔한 성씨(김이박최정 등)는 자동 조회되므로 clarify 불필요

[규칙]
- JSON만 출력하세요. 다른 텍스트 금지.
- internal_rag 선택 시 collections 배열 필수. 이름 추천 요청이면 반드시 ["hanja_col", "paper_col"] 함께 포함.
- 이미 실행한 Tool은 선택 금지.
- 정보가 충분하면 generate 선택.

예시:
{"next": "internal_rag", "collections": ["hanja_col", "paper_col"], "reason": "한자 이름 추천"}
{"next": "internal_rag", "collections": ["hanja_col", "paper_col"], "reason": "남녀 이름 추천, 유형 미지정이므로 한자로 간주"}
{"next": "clarify", "reason": "성별·이름유형 모두 불명확"}
{"next": "generate", "reason": "정보 충분"}"""


def _parse_router_response(raw: str, used_tools: list[str]) -> tuple[NextAction, list[str]]:
    """LLM 라우터 응답에서 next_action과 collections를 파싱합니다."""
    for pattern in (r"```json\s*(\{.*?\})\s*```", r"(\{[^{}]*\})"):
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                action = parsed.get("next", "")
                raw_cols = parsed.get("collections", [])
                collections = [c for c in raw_cols if c in _VALID_COLLECTIONS] if isinstance(raw_cols, list) else []
                if action in VALID_TOOLS and action not in used_tools:
                    return action, collections  # type: ignore[return-value]
                if action == "generate":
                    return "generate", []
            except (json.JSONDecodeError, AttributeError):
                continue
    return "generate", []


def llm_router_node(state: NamingState) -> NamingState:
    """LLM이 다음 실행할 Tool을 판단합니다."""
    iterations = state.get("iterations", 0)
    used_tools = state.get("used_tools", [])

    # 최대 반복 초과 시 강제 generate
    if iterations >= MAX_ITERATIONS:
        return {**state, "next_action": "generate", "iterations": iterations}

    # context 길이 제한 — 초과 시 앞부분 잘라냄
    context = state["context"]
    if len(context) > CONTEXT_MAX_CHARS:
        context = "...(앞부분 생략)...\n" + context[-CONTEXT_MAX_CHARS:]

    used_tools_str = ", ".join(used_tools) if used_tools else "없음"

    messages = [
        SystemMessage(content=_ROUTER_SYSTEM),
        HumanMessage(content=(
            f"[사용자 질문]\n{state['query']}\n\n"
            f"[이미 실행한 Tool]\n{used_tools_str}\n\n"
            f"[지금까지 수집된 정보 요약]\n{context if context else '없음'}\n\n"
            f"다음에 실행할 Tool을 JSON으로 답하세요."
        )),
    ]

    response = _llm_router.invoke(messages)
    raw = response.content.strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    next_action, collections = _parse_router_response(raw, used_tools)

    return {**state, "next_action": next_action, "collections": collections, "iterations": iterations + 1}


def route_selector(state: NamingState) -> NextAction:
    """LLM Router 결과를 엣지로 전달합니다."""
    return state["next_action"]


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────

def _parse_db_result(raw: str) -> str:
    """db_server JSON 반환값에서 message 필드를 추출합니다. 파싱 실패 시 raw 반환."""
    try:
        parsed = json.loads(raw)
        return parsed.get("message", raw)
    except (json.JSONDecodeError, TypeError):
        return raw


# ─────────────────────────────────────────────
# Tool 노드
# ─────────────────────────────────────────────

_NAME_RECOMMEND_KW = {"이름", "추천", "작명", "짓", "씨"}

def internal_rag_node(state: NamingState) -> NamingState:
    """LLM이 선택한 컬렉션을 ChromaDB에서 검색합니다."""
    query = state["query"]
    collections = state.get("collections") or ["hanja_col"]  # 폴백: hanja_col

    is_name_query = any(kw in query for kw in _NAME_RECOMMEND_KW)

    results = []
    for col in collections:
        if col == "hanja_col" and is_name_query:
            # 요청 이름 수에 비례해 샘플 크기 조정 (이름당 최소 3개 선택지)
            count_match = re.search(r'(\d+)\s*개', query)
            req_count = int(count_match.group(1)) if count_match else 3
            n_results = min(max(20, req_count * 3), 40)
            results.append(rag_server.sample_hanja(query, n_results=n_results))
        else:
            results.append(rag_server.search_rag(query, col))

    new_context = state["context"] + "\n\n[internal_rag 결과]\n" + "\n\n".join(results)
    return {**state, "context": new_context, "next_action": "llm_router",
            "collections": [], "used_tools": state.get("used_tools", []) + ["internal_rag"]}


def graph_db_node(state: NamingState) -> NamingState:
    """Neo4j에서 한자 오행 관계를 조회합니다."""
    result = graph_server.answer_graph_query(state["query"])
    new_context = state["context"] + "\n\n[graph_db 결과]\n" + result
    return {**state, "context": new_context, "next_action": "llm_router",
            "used_tools": state.get("used_tools", []) + ["graph_db"]}


def sql_db_node(state: NamingState) -> NamingState:
    """81수리 4격 계산 또는 吉수 조합 역산을 수행합니다."""
    query = state["query"]

    nums = re.findall(r"\b\d+\b", query)

    if any(kw in query for kw in {"어울리는 획수", "吉수", "획수 조합", "역산", "길한 획수"}):
        if nums:
            result = _parse_db_result(db_server.find_lucky_strokes(int(nums[0])))
        else:
            result = "[안내] 성씨 획수를 입력해주세요. 예: '김씨(8획)에 어울리는 吉수 조합'"

    elif any(kw in query for kw in {"오행 조합", "오행 궁합"}):
        ohaeng = re.findall(r"[木火土金水]", query)
        if len(ohaeng) >= 3:
            result = _parse_db_result(db_server.lookup_ohaeng_combo(ohaeng[0], ohaeng[1], ohaeng[2]))
        else:
            result = "[안내] 오행 조합 조회는 성씨·이름1·이름2의 오행(木/火/土/金/水)을 모두 입력해주세요."

    elif len(nums) >= 3:
        result = _parse_db_result(db_server.calculate_name_suri(int(nums[0]), int(nums[1]), int(nums[2])))

    elif nums:
        result = (
            f"[안내] 성씨 획수 {nums[0]}획 기준으로 吉수 조합을 역산합니다.\n"
            + _parse_db_result(db_server.find_lucky_strokes(int(nums[0])))
        )

    else:
        result = "[안내] 수리 계산을 위해 획수 정보가 필요합니다. 성씨와 이름 각 글자의 획수를 입력해주세요."

    new_context = state["context"] + "\n\n[sql_db 결과]\n" + result
    return {**state, "context": new_context, "next_action": "llm_router",
            "used_tools": state.get("used_tools", []) + ["sql_db"]}


def external_api_node(state: NamingState) -> NamingState:
    """국가법령정보 API 또는 우리말샘 API를 호출합니다."""
    query = state["query"]
    results = []

    if any(kw in query for kw in {"검증", "실제 단어", "존재하는"}):
        words = re.findall(r"['\"]([가-힣]+)['\"]", query)
        if not words:
            words = re.findall(r"([가-힣]{2,4})(?:이|가|은|는|이라는|라는)", query)
        for word in words[:3]:
            results.append(law_server.verify_korean_word(word))

    if any(kw in query for kw in {"법령", "조항", "조문", "가족관계", "출생신고", "인명용"}):
        results.append(law_server.search_law(query))

    if not results:
        results.append(law_server.search_law(query))

    new_context = state["context"] + "\n\n[external_api 결과]\n" + "\n\n".join(results)
    return {**state, "context": new_context, "next_action": "llm_router",
            "used_tools": state.get("used_tools", []) + ["external_api"]}


# ─────────────────────────────────────────────
# 반문 노드 — 이름 추천 조건 부족 시
# ─────────────────────────────────────────────

def clarify_node(state: NamingState) -> NamingState:
    """이름 추천에 필요한 정보(성별/이름 유형/성씨 한자)가 부족할 때 반문을 생성합니다."""
    query = state["query"]
    has_gender = any(kw in query for kw in _GENDER_KW)
    has_type = any(kw in query for kw in _TYPE_KW)

    # 성씨 한자 필요 여부 판단
    surname_kr, surname_info = _resolve_surname(query)
    needs_surname_hanja = bool(surname_kr and not surname_info)

    questions = []
    if not has_gender:
        questions.append("**성별이 어떻게 되나요?** (아들 / 딸)")
    if not has_type:
        questions.append("**어떤 종류의 이름을 원하시나요?** (한자 이름 / 순우리말 이름)")
    if needs_surname_hanja:
        questions.append(
            f"**'{surname_kr}씨' 성씨의 한자를 괄호 안에 표기해주세요.**\n"
            f"   다음과 같이 한자를 포함해서 다시 요청해주세요.\n"
            f"   예) '{surname_kr}씨(한자) 딸 한자 이름 추천해줘'\n"
            f"   한자를 모르신다면 네이버 한자사전 또는 성씨 검색을 활용해주세요."
        )

    if not questions:
        # 반문 조건 없음 → 바로 generate로 위임 (안전망)
        questions.append("**추가로 알려주실 정보가 있으신가요?** (성별, 원하는 뜻, 발음 등)")

    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))

    example_surname = surname_kr if surname_kr else "임"
    answer = (
        f"좋은 이름을 추천해드리기 위해 아래 정보를 알려주세요.\n\n"
        f"{numbered}\n\n"
        f"추가로 원하는 뜻·느낌(예: 밝은, 지혜로운, 강한)이나 선호하는 발음이 있으면 함께 알려주세요.\n\n"
        f"예시: \"{example_surname}씨 딸 한자 이름 추천해줘. 밝고 지혜로운 뜻이면 좋겠어.\""
    )
    return {**state, "answer": answer}


# ─────────────────────────────────────────────
# 답변 생성 노드
# ─────────────────────────────────────────────

_GENERATE_SYSTEM_SINGLE = """당신은 한국 작명 전문가 AI입니다.
사용자가 외자(이름 한 글자) 이름을 요청했습니다. 성씨 포함 총 2자 이름을 추천하세요.

⚠️ 핵심 제약 — 반드시 준수:
이름 글자에 쓰는 한자는 [참고 정보]의 한자 목록에서만 선택하세요.
[참고 정보]에 없는 한자를 임의로 추가하거나 획수·오행을 추측하는 것은 엄격히 금지됩니다.

한자 외자 이름 기준 (반드시 준수):
- 이름: 1글자(한자 1개). 성씨 포함 총 2자.
- 한자의 독음(한글 발음)이 원하는 글자와 반드시 일치
- [참고 정보] 목록에 있는 한자만 사용. 목록에 없으면 다른 글자 선택.
- 이름 글자 한글 표기 절대 금지.
- 다음 유형의 한자는 절대 사용 금지:
  · 성별·신체 지칭: 女(여), 男(남), 耳(이), 口(구) 등
  · 부정적 뜻: "계집", "종", "죽음", "어둠" 등
  · 동식물 직접 지칭: 桃(도), 梅(매), 菊(국), 竹(죽), 鳥(조), 犬(견) 등
  · 신체 외형: 肥(비), 瘦(수) 등
  · 현대 이름에 거의 쓰이지 않는 구식 한자: 苟(구), 押(압), 矧(신) 등
- [참고 정보]에 [성씨 정보]가 있으면 오행 흐름 계산에 반드시 사용하세요.

출력 형식 (반드시 이 형식으로만 작성):
## [이름 N] 전체이름 (이름한자)
**추천 이유**: 추천 근거 1~2문장.
**한자 풀이**:
- 이름 글자(한자) — 뜻, 실제획수 [자원오행]
**오행 흐름**: 성씨(X오행) → 이름(X오행) — 상생/중립/상극

괄호 안 한자 표기: 이름 1글자 한자만. 성씨 한자 포함 금지. 예) 김빛 → (昺)
[성씨 정보]에 오행이 있으면 실제 오행으로 표기. 없으면 '성씨(오행미상)'으로 표기.
수리: [참고 정보]에 [sql_db 결과] 섹션이 있고 수치가 있으면 오행 흐름 다음 줄에 **수리**: 원격N격(吉/凶), 형격N격(吉/凶) 추가. 없으면 수리 줄 작성 금지.

(이름 개수만큼 반복)
---
⚠️ 면책 고지: 추천 이름의 출생신고 가능 여부를 100% 보장하지 않습니다. 최종 확인은 관할 기관을 통해 진행하세요.

규칙:
- 위 형식 외 내용 추가 금지. 추천 이름은 요청 개수만큼만 작성.
- 한자·획수·오행은 반드시 [참고 정보]에 명시된 값만 사용.
- 답변은 한국어로 작성하세요."""


_GENERATE_SYSTEM = """당신은 한국 작명 전문가 AI입니다.
사용자의 질문과 제공된 참고 정보를 바탕으로 정확하고 친절하게 답변하세요.

⚠️ 핵심 제약 — 반드시 준수:
이름 추천 시 사용하는 모든 한자는 [참고 정보]에 명시된 한자 목록에서만 선택하세요.
[참고 정보]에 없는 한자를 임의로 추가하거나 획수·오행을 추측하는 것은 엄격히 금지됩니다.
컨텍스트에 제공된 한자가 부족하더라도 있는 것만으로 이름을 구성하세요.

한자 이름 추천 기준 (반드시 준수):
- 이름 글자 수: 기본 2글자(성씨 제외). 단, 사용자가 "외자", "한 글자", "1글자"를 요청하면 1글자(성씨 포함 총 2자) 추천.
- 각 한자의 독음(한글 발음)이 원하는 이름 글자와 반드시 일치해야 함.
- 성씨와 이름의 첫 글자 발음이 같으면 절대 사용하지 마세요. 예) 양씨 → "양○" 불가
- 이름 글자 모두 [참고 정보] 목록에 있는 한자로만 표기. 한 글자라도 목록에 없으면 다른 조합 선택. 한글 표기 절대 금지.
- 다음 유형의 한자는 절대 사용 금지:
  · 성별·신체 지칭: 女(여), 男(남), 耳(이), 口(구) 등
  · 부정적 뜻: "계집", "종", "죽음", "어둠" 등
  · 동식물 직접 지칭: 桃(도·복숭아), 梅(매·매화), 菊(국·국화), 竹(죽·대나무), 鳥(조·새), 犬(견·개) 등
  · 신체 외형: 肥(비·살찔), 瘦(수·마를) 등
  · 현대 이름에 거의 쓰이지 않는 구식 한자: 苟(구), 押(압), 矧(신) 등
- [paper_col 결과]가 있으면 최근 트렌드·선호 음절·성별 경향을 적극 반영하세요.
- 다음 이름은 너무 흔하므로 추천 금지: 지은, 서연, 서윤, 민준, 서준, 지우, 하준, 유나, 은서, 채원, 수아, 지아, 하은, 민서, 예린. 대신 덜 흔하면서 뜻이 좋은 한자를 창의적으로 조합하세요.

출력 형식 (반드시 이 형식으로만 작성):
## [이름 N] 전체이름 (이름두글자한자)
**추천 이유**: 추천 근거 1~2문장. paper_col 트렌드·오행·수리 등 구체적 근거 포함.
**한자 풀이**:
- 첫째 글자(한자) — 뜻, 실제획수 [자원오행]
- 둘째 글자(한자) — 뜻, 실제획수 [자원오행]
**오행 흐름**: 성씨(X오행) → 첫째(X오행) → 둘째(X오행) — 상생/중립/상극

괄호 안 한자 표기: 이름 2글자 한자만. 성씨 한자 포함 금지. 예) 김승기 → (承技)
[성씨 정보]에 오행이 있으면 '성씨(X오행)'에 실제 오행을 표기. 없으면 '성씨(오행미상)'으로 표기.
수리: [참고 정보]에 [sql_db 결과] 섹션이 있고 수치가 있으면 오행 흐름 다음 줄에 **수리**: 원격N격(吉/凶), 형격N격(吉/凶) 추가. 없으면 수리 줄 작성 금지.

(이름 개수만큼 반복)
---
⚠️ 면책 고지: 추천 이름의 출생신고 가능 여부를 100% 보장하지 않습니다. 최종 확인은 관할 기관을 통해 진행하세요.

규칙:
- 위 형식 외 내용 추가 금지. 추천 이름은 요청 개수만큼만 작성.
- 한자·획수·오행은 반드시 [참고 정보]에 명시된 값만 사용. 컨텍스트에 없는 한자는 절대 추천 금지.
- "N획", "?" 같은 플레이스홀더 사용 금지.
- 답변은 한국어로 작성하세요."""


def generate_node(state: NamingState) -> NamingState:
    """수집된 context를 바탕으로 LLM이 최종 답변을 생성합니다."""
    query = state["query"]

    # 외자 요청 감지
    is_single = any(kw in query for kw in _SINGLE_KW)

    # 성씨 한자 자동 해결
    surname_kr, surname_info = _resolve_surname(query)
    surname_context = ""
    if surname_kr and surname_info:
        hanja = surname_info["hanja"]
        ohaeng = surname_info.get("resource_ohaeng", "")
        strokes = surname_info.get("strokes", 0)
        if ohaeng:
            surname_context = (
                f"[성씨 정보] {surname_kr}씨 한자: {hanja} "
                f"(자원오행: {ohaeng}오행, {strokes}획)\n\n"
            )
        else:
            surname_context = f"[성씨 정보] {surname_kr}씨 한자: {hanja} (오행 미상)\n\n"

    context_with_surname = surname_context + state["context"] if surname_context else state["context"]
    system = _GENERATE_SYSTEM_SINGLE if is_single else _GENERATE_SYSTEM

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=(
            f"[참고 정보]\n{context_with_surname}\n\n"
            f"[질문]\n{query}"
        )),
    ]

    response = _llm_generate.invoke(messages)

    # <think>...</think> 태그 제거
    answer = re.sub(r"<think>.*?</think>", "", response.content, flags=re.DOTALL).strip()
    disclaimer = "\n---\n⚠️ 면책 고지: 추천 이름의 출생신고 가능 여부를 100% 보장하지 않습니다. 최종 확인은 관할 기관을 통해 진행하세요."
    if "⚠️" not in answer:
        answer += disclaimer
    return {**state, "answer": answer}


# ─────────────────────────────────────────────
# 그래프 조립
# ─────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(NamingState)

    # 노드 등록
    graph.add_node("llm_router", llm_router_node)
    graph.add_node("internal_rag", internal_rag_node)
    graph.add_node("graph_db", graph_db_node)
    graph.add_node("sql_db", sql_db_node)
    graph.add_node("external_api", external_api_node)
    graph.add_node("generate", generate_node)
    graph.add_node("clarify", clarify_node)

    # 진입점
    graph.set_entry_point("llm_router")

    # llm_router → 6방향 조건 분기
    graph.add_conditional_edges(
        "llm_router",
        route_selector,
        {
            "internal_rag": "internal_rag",
            "graph_db": "graph_db",
            "sql_db": "sql_db",
            "external_api": "external_api",
            "generate": "generate",
            "clarify": "clarify",
        },
    )

    # 각 Tool 노드 실행 후 → llm_router로 복귀 (ReAct 루프)
    # llm_router가 정보 충분 여부를 재판단 → generate 또는 추가 Tool 선택
    _tool_targets = {
        "llm_router": "llm_router",
        "internal_rag": "internal_rag",
        "graph_db": "graph_db",
        "sql_db": "sql_db",
        "external_api": "external_api",
        "generate": "generate",
        "clarify": "clarify",
    }
    for node in ["internal_rag", "graph_db", "sql_db", "external_api"]:
        graph.add_conditional_edges(node, route_selector, _tool_targets)

    # generate / clarify → 종료
    graph.add_edge("generate", END)
    graph.add_edge("clarify", END)

    return graph.compile()


# ─────────────────────────────────────────────
# 단독 실행 테스트
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = build_graph()

    test_cases = [
        "김씨 성에 木 오행이고 吉수인 한자 이름 추천해줘",
        "木火土 오행 조합의 상생 관계를 알려줘",
        "인명용 한자에 관한 법령을 찾아줘",
        "밝고 지혜로운 뜻의 한자를 추천해줘",
    ]

    for query in test_cases:
        print(f"\n질문: {query}")
        result = app.invoke({
            "query": query,
            "context": "",
            "next_action": "generate",
            "answer": "",
            "iterations": 0,
            "used_tools": [],
            "collections": [],
            "name_length": 2,
            "surname_hanja": "",
        })
        print(f"반복 횟수: {result['iterations']}")
        print(f"답변: {result['answer'][:120]}...")
        print("-" * 60)
