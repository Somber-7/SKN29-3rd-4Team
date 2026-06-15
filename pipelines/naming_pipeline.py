"""
title: 작명 QA
author: SKN29-3rd-4Team
version: 1.0.0
description: 한자/수리/오행/법령/순우리말 기반 LangGraph 작명 파이프라인
"""
import re
import sys
import types


def _stub_fastmcp():
    """MCP 서버 파일들이 fastmcp 없이 import될 수 있도록 스텁을 주입합니다.
    컨테이너에서는 MCP 서버를 구동하지 않고 함수만 직접 호출하므로 스텁으로 충분합니다."""
    if "fastmcp" in sys.modules:
        return
    mod = types.ModuleType("fastmcp")

    class FastMCP:
        def __init__(self, *a, **kw):
            pass

        def tool(self, f=None, **kw):
            return f if f is not None else (lambda fn: fn)

        def run(self):
            pass

    mod.FastMCP = FastMCP
    sys.modules["fastmcp"] = mod


class Pipeline:
    def __init__(self):
        self.name = "작명 QA"
        self.app = None

    async def on_startup(self):
        _stub_fastmcp()
        sys.path.insert(0, "/app/src_naming/mcp")
        sys.path.insert(0, "/app/src_naming/graph")
        from naming_graph import build_graph
        self.app = build_graph()

    async def on_shutdown(self):
        pass

    # 이름 추천 연속 요청 감지 키워드
    _CONTINUATION_KW = {"더 추천", "2개 더", "3개 더", "하나 더", "몇 개 더", "추가로 추천", "다른 이름"}
    _NAME_CONTEXT_KW = {"이름", "추천", "씨", "딸", "아들", "한자", "순우리말", "짓"}
    _NAME_REQUEST_KW = {"이름", "작명", "추천", "짓고"}
    # assistant 출력 포맷에서 추천 이름을 추출하는 패턴 ("## [이름 N] 전체이름 (한자)")
    _RECOMMENDED_PATTERN = re.compile(r"##\s*\[이름\s*\d+\]\s*([가-힣]{2,5})")

    def _extract_prev_names(self, messages: list) -> list[str]:
        """이전 assistant 응답에서 추천된 이름 목록을 추출합니다."""
        names = []
        for msg in messages:
            if msg.get("role") == "assistant":
                names.extend(self._RECOMMENDED_PATTERN.findall(msg.get("content", "")))
        return names

    def _resolve_query(self, user_message: str, messages: list) -> str:
        """대화 이력을 바탕으로 그래프에 전달할 완전한 쿼리를 구성합니다."""
        if len(messages) < 2:
            return user_message

        # 직전 assistant 응답과 그 이전 user 질문 추출
        prev_assistant = None
        prev_user = None
        for msg in reversed(messages[:-1]):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "assistant" and prev_assistant is None:
                prev_assistant = content
            elif role == "user" and prev_user is None:
                prev_user = content
            if prev_assistant is not None and prev_user is not None:
                break

        # 케이스 1: clarify 반문에 대한 응답 → 원본 질문 + 보충 정보 합성
        if prev_assistant and "아래 정보를 알려주세요" in prev_assistant and prev_user:
            return f"{prev_user} {user_message}"

        # 케이스 2: 추가 추천 요청 ("2개 더", "하나 더 추천" 등)
        # → 이전 대화에서 이름 추천 관련 user 메시지를 수집해 맥락 제공
        if any(kw in user_message for kw in self._CONTINUATION_KW):
            name_msgs = [
                m.get("content", "").strip()
                for m in messages[:-1]
                if m.get("role") == "user"
                and any(kw in m.get("content", "") for kw in self._NAME_CONTEXT_KW)
            ]
            prev_names = self._extract_prev_names(messages[:-1])
            parts = []
            if name_msgs:
                parts.append(f"[이름 추천 맥락: {' / '.join(name_msgs)}]")
            if prev_names:
                parts.append(f"[이미 추천한 이름: {', '.join(prev_names)}]")
            parts.append(f"추가 요청: {user_message} (앞서 추천한 이름과 겹치지 않는 새 이름으로)")
            return " ".join(parts)

        # 케이스 3: 이름 추천 요청 + 이전 대화에 추천 이력이 있으면 중복 방지 문구 추가
        if any(kw in user_message for kw in self._NAME_REQUEST_KW):
            prev_names = self._extract_prev_names(messages[:-1])
            if prev_names:
                return (
                    f"{user_message} "
                    f"(이미 추천한 이름 [{', '.join(prev_names)}]과 겹치지 않는 다른 이름으로)"
                )

        return user_message

    async def pipe(self, user_message: str, model_id: str, messages: list, body: dict) -> str:
        if self.app is None:
            return "[오류] 파이프라인이 초기화되지 않았습니다."

        query = self._resolve_query(user_message, messages)

        state = {
            "query": query,
            "context": "",
            "next_action": "generate",
            "answer": "",
            "iterations": 0,
            "used_tools": [],
            "collections": [],
            "name_length": 2,
            "surname_hanja": "",
        }
        result = await self.app.ainvoke(state)
        return result.get("answer", "").strip()
