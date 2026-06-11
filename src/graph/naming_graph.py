"""
naming_graph.py — 작명 QA LangGraph ReAct StateGraph

[구조]
  LLM Router가 필요한 Tool을 판단 → Tool 실행 → 결과를 LLM에 전달
  → 추가 Tool 필요 여부 재판단 (루프) → 충분하면 최종 답변 생성

[노드 구성]
  llm_router   — LLM이 다음 실행할 Tool 결정 (또는 답변 생성 판단)
  internal_rag — ChromaDB 벡터 검색 (한자/수리/오행/법령/순우리말)
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
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
import rag_server
import db_server
import law_server


# ─────────────────────────────────────────────
# State 정의
# ─────────────────────────────────────────────

NextAction = Literal["internal_rag", "graph_db", "sql_db", "external_api", "generate"]

MAX_ITERATIONS = 5   # 무한 루프 방지
CONTEXT_MAX_CHARS = 8000  # context 누적 상한 (토큰 초과 방지)
VALID_TOOLS = {"internal_rag", "graph_db", "sql_db", "external_api", "generate"}


class NamingState(TypedDict):
    query: str              # 사용자 원본 질문
    context: str            # 누적된 Tool 실행 결과
    next_action: NextAction # LLM이 결정한 다음 액션
    answer: str             # 최종 답변
    iterations: int         # 현재 반복 횟수
    used_tools: list[str]   # 이미 실행한 Tool 이력 (중복 방지)


# ─────────────────────────────────────────────
# LLM 초기화
# ─────────────────────────────────────────────

_llm = ChatOllama(
    model="qwen3:4b",
    num_ctx=32768,
    temperature=0.3,
)

# ─────────────────────────────────────────────
# LLM Router 노드
# ─────────────────────────────────────────────

_ROUTER_SYSTEM = """당신은 작명 QA 시스템의 라우터입니다.
사용자 질문과 지금까지 수집된 정보를 보고 다음에 실행할 Tool을 결정하세요.

[사용 가능한 Tool]
- internal_rag  : 한자 뜻/추천, 수리 운세 설명, 오행 조합 설명, 법령 조문, 순우리말 이름 검색
- sql_db        : 획수 수치 계산, 81수리 4격 계산, 吉수 획수 조합 역산, 오행 조합 운세 수치 조회
- external_api  : 국가법령정보 실시간 조회, 순우리말 단어 존재 여부 검증
- graph_db      : 한자-오행 관계 탐색, 상생/상극 경로 탐색 (Neo4j)
- generate      : 지금까지 수집된 정보로 최종 답변 생성 (정보가 충분할 때만 선택)

[규칙]
- 반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 절대 포함하지 마세요.
- 이미 실행한 Tool 목록에 있는 Tool은 선택하지 마세요.
- 필요한 Tool을 모두 실행했거나 정보가 충분하면 generate를 선택하세요.

{"next": "tool이름", "reason": "선택 이유 한 줄"}"""


def _parse_next_action(raw: str, used_tools: list[str]) -> NextAction:
    """LLM 응답에서 next_action을 파싱합니다. 실패 시 generate 반환."""
    # JSON 블록 추출 시도 (```json ... ``` 또는 { ... })
    for pattern in (r"```json\s*(\{.*?\})\s*```", r"(\{[^{}]*\})"):
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                action = parsed.get("next", "")
                if action in VALID_TOOLS and action not in used_tools:
                    return action  # type: ignore[return-value]
                if action == "generate":
                    return "generate"
            except (json.JSONDecodeError, AttributeError):
                continue
    return "generate"


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

    response = _llm.invoke(messages)
    raw = response.content.strip()

    # <think>...</think> 태그 제거 (Qwen 추론 모드 대응)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    next_action = _parse_next_action(raw, used_tools)

    return {**state, "next_action": next_action, "iterations": iterations + 1}


def route_selector(state: NamingState) -> NextAction:
    """LLM Router 결과를 엣지로 전달합니다."""
    return state["next_action"]


# ─────────────────────────────────────────────
# Tool 노드
# ─────────────────────────────────────────────

def internal_rag_node(state: NamingState) -> NamingState:
    """ChromaDB에서 한자/수리/오행/법령/순우리말 문서를 검색합니다."""
    query = state["query"]
    results = []

    if any(kw in query for kw in {"수리", "4격", "원격", "형격", "이격", "정격", "운세"}):
        results.append(rag_server.search_rag(query, "suri_col"))
    if any(kw in query for kw in {"오행", "상생", "상극", "木", "火", "土", "金", "水"}):
        results.append(rag_server.search_rag(query, "ohaeng_col"))
    if any(kw in query for kw in {"한자", "획수", "뜻", "음", "독음", "추천"}):
        results.append(rag_server.search_rag(query, "hanja_col"))
    if any(kw in query for kw in {"법령", "조항", "조문", "출생신고", "인명용"}):
        results.append(rag_server.search_rag(query, "law_col"))
    if any(kw in query for kw in {"순우리말", "우리말", "이름 뜻", "이름 추천"}):
        results.append(rag_server.search_rag(query, "urimalsam_col"))
    if not results:
        results.append(rag_server.search_rag(query, "hanja_col"))

    new_context = state["context"] + "\n\n[internal_rag 결과]\n" + "\n\n".join(results)
    return {**state, "context": new_context, "next_action": "generate",
            "used_tools": state.get("used_tools", []) + ["internal_rag"]}


def graph_db_node(state: NamingState) -> NamingState:
    """Neo4j에서 한자 오행 관계를 조회합니다."""
    # graph_server.py 팀원 작업 완료 후 연결 예정
    result = "[graph_db] Neo4j 연결 대기 중 (팀원 작업 후 활성화)"
    new_context = state["context"] + "\n\n[graph_db 결과]\n" + result
    return {**state, "context": new_context, "next_action": "generate",
            "used_tools": state.get("used_tools", []) + ["graph_db"]}


def sql_db_node(state: NamingState) -> NamingState:
    """81수리 4격 계산 또는 吉수 조합 역산을 수행합니다."""
    query = state["query"]

    if any(kw in query for kw in {"어울리는 획수", "吉수", "획수 조합", "역산"}):
        nums = re.findall(r"\d+", query)
        surname_strokes = int(nums[0]) if nums else 8
        result = db_server.find_lucky_strokes(surname_strokes)
    elif any(kw in query for kw in {"오행 조합", "오행 궁합"}):
        ohaeng = re.findall(r"[木火土金水]", query)
        if len(ohaeng) >= 3:
            result = db_server.lookup_ohaeng_combo(ohaeng[0], ohaeng[1], ohaeng[2])
        else:
            result = "[안내] 오행 조합 조회는 성씨·이름1·이름2의 오행(木/火/土/金/水)을 모두 입력해주세요."
    else:
        nums = re.findall(r"\d+", query)
        if len(nums) >= 3:
            result = db_server.calculate_name_suri(int(nums[0]), int(nums[1]), int(nums[2]))
        else:
            result = db_server.find_lucky_strokes(int(nums[0]) if nums else 8)

    new_context = state["context"] + "\n\n[sql_db 결과]\n" + result
    return {**state, "context": new_context, "next_action": "generate",
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
    return {**state, "context": new_context, "next_action": "generate",
            "used_tools": state.get("used_tools", []) + ["external_api"]}


# ─────────────────────────────────────────────
# 답변 생성 노드
# ─────────────────────────────────────────────

_GENERATE_SYSTEM = """당신은 한국 작명 전문가 AI입니다.
사용자의 질문과 제공된 참고 정보를 바탕으로 정확하고 친절하게 답변하세요.

규칙:
- 참고 정보에 없는 내용은 추측하지 마세요.
- 한자 이름 추천 시 획수(원획법), 오행, 수리 4격을 함께 설명하세요.
- 법령 관련 내용은 출처(조문 번호)를 명시하세요.
- 출처는 [한자: 자원오행표], [법령: 가족관계등록법 제44조], [수리: 81수리 16격 吉] 형식으로 표기하세요.
- 추천 이름의 출생신고 가능 여부를 100% 보장하지 않습니다. 반드시 면책 고지를 포함하세요.
- 답변은 한국어로 작성하세요."""


def generate_node(state: NamingState) -> NamingState:
    """수집된 context를 바탕으로 LLM이 최종 답변을 생성합니다."""
    messages = [
        SystemMessage(content=_GENERATE_SYSTEM),
        HumanMessage(content=(
            f"[참고 정보]\n{state['context']}\n\n"
            f"[질문]\n{state['query']}"
        )),
    ]

    response = _llm.invoke(messages)

    # <think>...</think> 태그 제거
    answer = re.sub(r"<think>.*?</think>", "", response.content, flags=re.DOTALL).strip()
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

    # 진입점
    graph.set_entry_point("llm_router")

    # llm_router → 5방향 조건 분기
    graph.add_conditional_edges(
        "llm_router",
        route_selector,
        {
            "internal_rag": "internal_rag",
            "graph_db": "graph_db",
            "sql_db": "sql_db",
            "external_api": "external_api",
            "generate": "generate",
        },
    )

    # 각 Tool 노드 실행 후 → llm_router로 복귀 (ReAct 루프)
    for node in ["internal_rag", "graph_db", "sql_db", "external_api"]:
        graph.add_edge(node, "llm_router")

    # generate → 종료
    graph.add_edge("generate", END)

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
        })
        print(f"반복 횟수: {result['iterations']}")
        print(f"답변: {result['answer'][:120]}...")
        print("-" * 60)
