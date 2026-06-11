"""
naming_graph.py — 작명 QA LangGraph StateGraph

사용자 입력을 4개 노드로 라우팅하고 최종 답변을 생성합니다.

[노드 구성]
  router       — 입력 의도 분석 후 경로 결정
  internal_rag — ChromaDB 벡터 검색 (수리/오행/법령 문서)
  graph_db     — Neo4j 한자 관계 그래프 조회
  sql_db       — SQLite 한자 속성 조회 (획수/오행/뜻)
  external_api — 국가법령정보 / 우리말샘 API 호출
  generate     — 수집된 context로 최종 답변 생성

[라우팅 기준]
  "획수", "수리", "4격", "운" 포함      → sql_db
  "오행", "상생", "상극", "관계" 포함   → graph_db
  "법령", "조항", "법", "순우리말" 포함 → external_api
  나머지 (한자 뜻/추천 등)             → internal_rag
"""

from __future__ import annotations

from typing import TypedDict, Literal
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END


# ─────────────────────────────────────────────
# State 정의
# ─────────────────────────────────────────────

RouteType = Literal["internal_rag", "graph_db", "sql_db", "external_api"]


class NamingState(TypedDict):
    query: str                  # 사용자 원본 질문
    route: RouteType | None     # 라우터가 결정한 경로
    context: str                # 각 노드가 수집한 참고 정보
    answer: str                 # generate 노드가 생성한 최종 답변


# ─────────────────────────────────────────────
# 라우터 노드
# ─────────────────────────────────────────────

_SQL_KEYWORDS = {"획수", "수리", "4격", "원격", "형격", "이격", "정격", "초년운", "청년운", "중년운", "총운"}
_GRAPH_KEYWORDS = {"오행", "상생", "상극", "목화토금수", "관계", "木", "火", "土", "金", "水"}
_LAW_KEYWORDS = {"법령", "조항", "조문", "가족관계", "출생신고", "인명용", "순우리말", "우리말"}


def router_node(state: NamingState) -> NamingState:
    """키워드 기반으로 라우팅 경로를 결정합니다."""
    query = state["query"]

    if any(kw in query for kw in _SQL_KEYWORDS):
        route: RouteType = "sql_db"
    elif any(kw in query for kw in _GRAPH_KEYWORDS):
        route = "graph_db"
    elif any(kw in query for kw in _LAW_KEYWORDS):
        route = "external_api"
    else:
        route = "internal_rag"

    return {**state, "route": route}


def route_selector(state: NamingState) -> RouteType:
    """라우터 결과를 엣지로 전달하는 조건 함수."""
    return state["route"]


# ─────────────────────────────────────────────
# 데이터 수집 노드 (뼈대 — 팀원 구현 연결 예정)
# ─────────────────────────────────────────────

def internal_rag_node(state: NamingState) -> NamingState:
    """ChromaDB에서 수리/오행/법령 문서를 검색합니다."""
    # TODO: rag_server.py의 search_rag Tool 연결
    context = f"[internal_rag] '{state['query']}' 검색 결과 (미구현 — ChromaDB 연결 필요)"
    return {**state, "context": context}


def graph_db_node(state: NamingState) -> NamingState:
    """Neo4j에서 한자 오행 관계를 조회합니다."""
    # TODO: Neo4j 드라이버 연결 (팀원 인프라 구축 후)
    context = f"[graph_db] '{state['query']}' 오행 관계 조회 결과 (미구현 — Neo4j 연결 필요)"
    return {**state, "context": context}


def sql_db_node(state: NamingState) -> NamingState:
    """SQLite에서 한자 획수/오행/뜻을 조회합니다."""
    # TODO: db_server.py의 calculate_name_suri / find_lucky_strokes Tool 연결
    context = f"[sql_db] '{state['query']}' 획수/수리 조회 결과 (미구현 — SQLite 연결 필요)"
    return {**state, "context": context}


def external_api_node(state: NamingState) -> NamingState:
    """국가법령정보 API 또는 우리말샘 API를 호출합니다."""
    # TODO: law_server.py의 search_law / verify_korean_word Tool 연결
    context = f"[external_api] '{state['query']}' 법령/우리말샘 조회 결과 (미구현 — API 키 필요)"
    return {**state, "context": context}


# ─────────────────────────────────────────────
# 답변 생성 노드
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = """당신은 한국 작명 전문가 AI입니다.
사용자의 질문과 제공된 참고 정보를 바탕으로 정확하고 친절하게 답변하세요.

규칙:
- 참고 정보에 없는 내용은 추측하지 마세요.
- 한자 이름 추천 시 획수(원획법), 오행, 수리 4격을 함께 설명하세요.
- 법령 관련 내용은 출처(조문 번호)를 명시하세요.
- 답변은 한국어로 작성하세요."""


def generate_node(state: NamingState) -> NamingState:
    """수집된 context를 바탕으로 LLM이 최종 답변을 생성합니다."""
    llm = ChatOllama(
        model="qwen3.5:4b",
        num_ctx=32768,
        temperature=0.7,
    )

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=(
            f"[참고 정보]\n{state['context']}\n\n"
            f"[질문]\n{state['query']}"
        )),
    ]

    response = llm.invoke(messages)
    return {**state, "answer": response.content}


# ─────────────────────────────────────────────
# 그래프 조립
# ─────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(NamingState)

    # 노드 등록
    graph.add_node("router", router_node)
    graph.add_node("internal_rag", internal_rag_node)
    graph.add_node("graph_db", graph_db_node)
    graph.add_node("sql_db", sql_db_node)
    graph.add_node("external_api", external_api_node)
    graph.add_node("generate", generate_node)

    # 진입점
    graph.set_entry_point("router")

    # router → 4방향 조건 분기
    graph.add_conditional_edges(
        "router",
        route_selector,
        {
            "internal_rag": "internal_rag",
            "graph_db": "graph_db",
            "sql_db": "sql_db",
            "external_api": "external_api",
        },
    )

    # 각 데이터 노드 → generate
    for node in ["internal_rag", "graph_db", "sql_db", "external_api"]:
        graph.add_edge(node, "generate")

    # generate → 종료
    graph.add_edge("generate", END)

    return graph.compile()


# ─────────────────────────────────────────────
# 단독 실행 테스트
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = build_graph()

    test_cases = [
        "김씨 성에 어울리는 획수 조합 추천해줘",
        "木火土 오행 조합의 상생 관계를 알려줘",
        "인명용 한자에 관한 법령을 찾아줘",
        "밝고 지혜로운 뜻의 한자를 추천해줘",
    ]

    for query in test_cases:
        result = app.invoke({"query": query, "route": None, "context": "", "answer": ""})
        print(f"질문: {query}")
        print(f"경로: {result['route']}")
        print(f"답변: {result['answer'][:80]}...")
        print("-" * 60)
