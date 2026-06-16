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
    _CONTINUATION_KW = {"더 추천", "2개 더", "3개 더", "하나 더", "몇 개 더", "추가로 추천", "다른 이름", "추가로", "추가"}
    _NAME_CONTEXT_KW = {"이름", "추천", "씨", "딸", "아들", "한자", "순우리말", "짓"}
    _NAME_REQUEST_KW = {"이름", "작명", "추천", "짓고"}
    # 이름 유형 전환 키워드 (한자 ↔ 순우리말)
    _NAME_TYPE_KW = frozenset({"순우리말", "우리말이름", "우리말 이름", "한글이름", "한글 이름"})
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

        # 케이스 1: clarify 반문에 대한 응답 → 원본 이름 요청 + 모든 보충 정보 합성
        # 연속 clarify가 2회 이상 발동될 경우 prev_user는 보충 답변이고 원본이 아님.
        # 대화 전체에서 "씨/이름/추천"이 포함된 가장 오래된 user 메시지를 원본으로 사용.
        if prev_assistant and "아래 정보를 알려주세요" in prev_assistant and prev_user:
            original_req = None
            for msg in messages[:-1]:
                if msg.get("role") == "user" and any(kw in msg.get("content", "") for kw in self._NAME_CONTEXT_KW):
                    original_req = msg.get("content", "")
                    break
            if original_req and original_req != prev_user:
                # 원본 + 보충 답변(prev_user) + 현재 메시지
                return f"{original_req} {prev_user} {user_message}"
            return f"{prev_user} {user_message}"

        # 케이스 2: 추가 추천 요청 ("추가로 3개만", "2개 더" 등)
        # → 원본 이름 요청 메시지를 찾아 새 개수로 재구성한 자연어 쿼리 반환
        if any(kw in user_message for kw in self._CONTINUATION_KW):
            is_type_switch = any(kw in user_message for kw in self._NAME_TYPE_KW)
            prev_names = self._extract_prev_names(messages[:-1])
            count_match = re.search(r"(\d+)\s*개", user_message)
            count_str = count_match.group(0) if count_match else "3개"

            if is_type_switch:
                # 유형 전환 ("순우리말 이름도 추가로"): 성씨 포함된 원본 요청 + 새 유형으로 재구성
                surname_base = None
                for msg in reversed(messages[:-1]):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if "씨" in content and any(kw in content for kw in self._NAME_CONTEXT_KW):
                            surname_base = content
                            break

                # 성씨 한자가 포함된 사용자 메시지 탐색 (clarify 응답)
                hanja_suffix = ""
                for msg in reversed(messages[:-1]):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        hm = re.search(r"[一-鿿]{1,2}", content)
                        if hm:
                            hanja_suffix = f" 성씨 한자: {hm.group()}"
                            break

                type_kw = next((kw for kw in self._NAME_TYPE_KW if kw in user_message), "순우리말")
                base = re.sub(r"(한자\s*이름|순우리말\s*이름|우리말\s*이름|한글\s*이름)", "", surname_base or "이름").strip()
                query = f"{base} {type_kw} {count_str} 추천해줘{hanja_suffix}"
                if prev_names:
                    query += f" (이미 추천한 이름: {', '.join(prev_names)} 제외)"
                return re.sub(r"\s+", " ", query).strip()

            # 같은 유형 추가 추천: 원본 요청에서 새 개수로 재구성
            original_req = None
            for msg in reversed(messages[:-1]):
                if msg.get("role") == "user" and any(
                    kw in msg.get("content", "") for kw in self._NAME_REQUEST_KW
                ):
                    original_req = msg.get("content", "").strip()
                    break

            if original_req:
                base = re.sub(r"(하나만?|한\s*개만?|\d+\s*개만?)", "", original_req)
                base = re.sub(r"(추천\s*해\s*줘|추천\s*해\s*주세요|추천해)", "", base).strip()
                base = re.sub(r"\s+", " ", base).strip().rstrip(".")
                query = f"{base} {count_str} 추천해줘"
            else:
                query = f"이름 {count_str} 추천해줘"

            if prev_names:
                query += f" (이미 추천한 이름: {', '.join(prev_names)} 제외)"
            return query

        # 케이스 3: 이름 추천 요청 + 이전 대화에 추천 이력이 있으면 중복 방지 문구 추가
        if any(kw in user_message for kw in self._NAME_REQUEST_KW):
            prev_names = self._extract_prev_names(messages[:-1])
            if prev_names:
                return (
                    f"{user_message} "
                    f"(이미 추천한 이름 [{', '.join(prev_names)}]과 겹치지 않는 다른 이름으로)"
                )

        return user_message

    def pipe(self, user_message: str, model_id: str, messages: list, body: dict) -> str:
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
        result = self.app.invoke(state)
        return result.get("answer", "").strip()
