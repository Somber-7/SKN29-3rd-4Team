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
_KOREAN_NAME_KW = {"순우리말", "우리말이름", "우리말 이름", "한글이름", "한글 이름", "순한국어"}

# 성씨 오행 fallback 사전 (peoplehanja.json에 없는 성씨 포함, 259개)
_SURNAME_OHAENG: dict[str, dict] = {}
try:
    _SURNAME_OHAENG_PATH = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "processed", "surname_ohaeng.json"
    )
    with open(_SURNAME_OHAENG_PATH, encoding="utf-8") as _f:
        _SURNAME_OHAENG = json.load(_f)
except Exception:
    pass

# 81수리 등급 사전 {1~81: "吉"/"凶"/"大吉"/"半吉"/...}
_SURI_RATINGS: dict[int, str] = {}
try:
    _SURI_JSON_PATH = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "raw", "reference", "81suri.json"
    )
    with open(_SURI_JSON_PATH, encoding="utf-8") as _sf:
        _suri_raw = json.loads("".join(_sf.readlines()[1:]))  # 첫 줄 주석 제거
    for _entry in _suri_raw:
        if isinstance(_entry, list) and len(_entry) >= 4 and _entry[0]:
            _SURI_RATINGS[int(_entry[0])] = str(_entry[3])
except Exception:
    pass



_OHAENG_HJ_MAP = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}


def _extract_query_ohaeng(query: str) -> str:
    """쿼리에 오행 표현이 하나라도 있으면 True 판단용으로 반환합니다 (clarify 스킵 여부 판별)."""
    m = re.search(r'([木火土金水])오행', query)
    if m:
        return _OHAENG_HJ_MAP[m.group(1)]
    m = re.search(r'(목|화|토|금|수)오행', query)
    if m:
        return m.group(1)
    return ""


def _extract_surname_ohaeng(query: str) -> str:
    """'성에 火오행' 처럼 성씨에 결합된 오행만 추출합니다 (generate 시 성씨 오행 대체용)."""
    hj_map = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}
    m = re.search(r'성에\s*([木火土金水])오행', query)
    if m:
        return hj_map[m.group(1)]
    m = re.search(r'성에\s*(목|화|토|금|수)오행', query)
    if m:
        return m.group(1)
    return ""


def _resolve_surname(query: str) -> tuple[str, dict | None]:
    """쿼리에서 성씨 한자를 추출합니다.
    괄호 표기(○씨(漢字))가 있을 때만 info_dict을 반환합니다.
    자원오행은 hanja_col DB에서 조회합니다.
    Returns: (surname_korean, info_dict or None)
    """
    def _lookup_ohaeng(hanja: str) -> str:
        ohaeng = rag_server.get_hanja_ohaeng(hanja)
        if not ohaeng:
            ohaeng = _SURNAME_OHAENG.get(hanja, {}).get("resource_ohaeng", "")
        return ohaeng

    # 괄호 안 한자 직접 제공: "임씨(任)" 또는 "임(任)씨"
    m = re.search(r'([가-힣]{1,2})씨\s*[（\(]([一-鿿]{1,2})[）\)]', query)
    if not m:
        m = re.search(r'([가-힣]{1,2})\s*[（\(]([一-鿿]{1,2})[）\)]\s*씨', query)
    if m:
        surname_kr = m.group(1)
        hanja = m.group(2)
        return surname_kr, {"hanja": hanja, "resource_ohaeng": _lookup_ohaeng(hanja), "strokes": 0}

    # 성씨 독음 확인 후 단독 한자 탐색 (clarify 응답 합성 시: "임씨 ... 任")
    m = re.search(r'([가-힣]{1,2})씨', query)
    if not m:
        return "", None
    surname_kr = m.group(1)
    after = query[m.end():]
    fallback_hanja = None
    for hm in re.finditer(r'(?<![一-鿿])([一-鿿]{1,2})(?![一-鿿])', after):
        hanja = hm.group(1)
        # "木오행", "火오행" 같은 오행 수식어는 성씨 한자가 아님
        if after[hm.end():hm.end()+2] == '오행':
            continue
        ohaeng = _lookup_ohaeng(hanja)
        if ohaeng:
            return surname_kr, {"hanja": hanja, "resource_ohaeng": ohaeng, "strokes": 0}
        if fallback_hanja is None:
            fallback_hanja = hanja  # 오행 없어도 한자는 기억
    # 오행 조회 실패해도 한자가 있으면 반환 (clarify 재발동 방지)
    if fallback_hanja:
        return surname_kr, {"hanja": fallback_hanja, "resource_ohaeng": "", "strokes": 0}
    return surname_kr, None


# 오행 상생/상극 쌍 — build_graph() 시 Neo4j에서 동적 로드, 실패 시 이 값이 fallback
_SANGSAENG_PAIRS: set[tuple[str, str]] = {("목","화"), ("화","토"), ("토","금"), ("금","수"), ("수","목")}
_SANGGEUK_PAIRS:  set[tuple[str, str]] = {("목","토"), ("토","수"), ("수","화"), ("화","금"), ("금","목")}


def _rebuild_forbidden_next() -> dict[str, list[str]]:
    return {
        o: [b for a, b in _SANGGEUK_PAIRS if a == o] + [a for a, b in _SANGGEUK_PAIRS if b == o]
        for o in ("목", "화", "토", "금", "수")
    }


_FORBIDDEN_NEXT: dict[str, list[str]] = _rebuild_forbidden_next()


def _pair_relation(a: str, b: str) -> str:
    if a == b:
        return "중립"
    if (a, b) in _SANGSAENG_PAIRS:
        return "상생"
    # 상극은 방향 무관: 금克목이면 목→금도 상극
    if (a, b) in _SANGGEUK_PAIRS or (b, a) in _SANGGEUK_PAIRS:
        return "상극"
    return "중립"


def _overall_relation(relations: list[str]) -> str:
    if any(r == "상극" for r in relations):
        return "상극"
    if all(r == "상생" for r in relations):
        return "상생"
    return "중립"


def _correct_ohaeng_in_output(text: str) -> str:
    """LLM 출력에서 오행 흐름의 상생/상극/중립 판정을 교정합니다."""
    _OE = r"[금목화수토]"
    _PREFIX = r"[가-힣]{1,4}씨"  # "성씨", "임씨", "박씨" 등 모두 매칭

    def fix_3(m: re.Match) -> str:
        prefix, a, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
        result = _overall_relation([_pair_relation(a, b), _pair_relation(b, c)])
        return f"{prefix}({a}) → 첫째({b}) → 둘째({c}) — {result}"

    def fix_2(m: re.Match) -> str:
        prefix, a, b = m.group(1), m.group(2), m.group(3)
        return f"{prefix}({a}) → 이름({b}) — {_pair_relation(a, b)}"

    text = re.sub(
        rf"({_PREFIX})\(({_OE})\)\s*→\s*첫째\(({_OE})\)\s*→\s*둘째\(({_OE})\)\s*—\s*[^\n]+",
        fix_3, text
    )
    text = re.sub(
        rf"({_PREFIX})\(({_OE})\)\s*→\s*이름\(({_OE})\)\s*—\s*[^\n]+",
        fix_2, text
    )
    return text


def _fix_hanja_headers(text: str, surname_kr: str, surname_info: dict | None) -> str:
    """헤더에서 한자 이름을 한글 발음으로 교정합니다.
    ## [이름 1] 任雅恩 (雅恩) → ## [이름 1] 임야은 (雅恩)"""
    if not surname_kr or not surname_info:
        return text

    all_hanja = rag_server._load_person_name_hanja()
    h2r: dict[str, str] = {
        meta.get("hanja", ""): meta.get("hangul", "")
        for _, meta in all_hanja
        if meta and meta.get("hanja") and meta.get("hangul")
    }

    def fix_header(m: re.Match) -> str:
        n = m.group(1)
        name_part = m.group(2).strip()
        hanja_in_parens = m.group(3).strip()

        # 이미 한글이면 그대로
        if not re.search(r"[一-鿿]", name_part):
            return m.group(0)

        # 괄호 안 이름 한자의 발음으로 재구성
        hangul_chars = [h2r[ch] for ch in hanja_in_parens if ch in h2r]
        if len(hangul_chars) == len([ch for ch in hanja_in_parens if "一" <= ch <= "鿿"]):
            return f"## [이름 {n}] {surname_kr}{''.join(hangul_chars)} ({hanja_in_parens})"
        return m.group(0)

    return re.sub(
        r"##\s*\[이름\s*(\d+)\]\s*([가-힣一-鿿]{2,8})\s*\(([一-鿿]{1,4})\)",
        fix_header, text
    )


def _fix_broken_headers(text: str) -> str:
    """## [이름이름] 전체이름 (한자) 패턴을 ## [이름 N] 이름이름 (한자) 로 교정합니다."""
    counter = [0]

    def fix_header(m: re.Match) -> str:
        counter[0] += 1
        return f"## [이름 {counter[0]}] {m.group(1)} ({m.group(2)})"

    return re.sub(
        r"##\s*\[([가-힣]{2,5})\]\s*(?:전체이름|이름)\s*\(([^)\n]+)\)",
        fix_header, text
    )


def _split_name_sections(text: str) -> tuple[str, list[str]]:
    """이름 섹션을 preamble + 섹션 리스트로 분리합니다.
    텍스트가 ## [이름 N]으로 시작(leading \\n 없음)해도 올바르게 처리합니다."""
    normalized = '\n' + text if not text.startswith('\n') else text
    parts = re.split(r'(?=\n## \[이름 \d+\])', normalized)
    preamble = parts[0].lstrip('\n')
    sections = [p for p in parts[1:] if '## [이름' in p]
    return preamble, sections


def _drop_sanggeuk_names(text: str) -> str:
    """상극 이름 섹션을 제거하고 재번호화합니다 (fallback)."""
    preamble, sections = _split_name_sections(text)
    valid = [s for s in sections if '— 상극' not in s]
    if not valid:
        return text
    renumbered = []
    for i, sec in enumerate(valid, 1):
        sec = re.sub(r'(## \[이름 )\d+(\])', rf'\g<1>{i}\2', sec, count=1)
        renumbered.append(sec)
    return preamble + "".join(renumbered)


def _drop_duplicate_sound_names(text: str) -> str:
    """추천 이름 중 한글 발음이 중복된 섹션을 나중 것 기준으로 제거하고 재번호화합니다."""
    preamble, sections = _split_name_sections(text)
    seen_sounds: set[str] = set()
    valid: list[str] = []
    # 섹션 헤더: ## [이름 N] 전체이름 (한자)
    header_pattern = re.compile(r'##\s*\[이름\s*\d+\]\s*([가-힣]{2,5})')
    for sec in sections:
        m = header_pattern.search(sec)
        if not m:
            valid.append(sec)
            continue
        full_name = m.group(1)
        # 성 제외, 이름 부분 발음만 비교 (2글자 이름이면 마지막 2글자)
        name_sound = full_name[1:]  # 성 1글자 제거
        if name_sound not in seen_sounds:
            seen_sounds.add(name_sound)
            valid.append(sec)
    if len(valid) == len(sections):
        return text  # 중복 없음
    if not valid:
        return text
    renumbered = []
    for i, sec in enumerate(valid, 1):
        sec = re.sub(r'(## \[이름 )\d+(\])', rf'\g<1>{i}\2', sec, count=1)
        renumbered.append(sec)
    return preamble + "".join(renumbered)


def _drop_duplicate_hanja_names(text: str) -> str:
    """추천 이름들 사이에 같은 한자가 중복으로 사용된 경우, 나중 이름을 제거하고 재번호화합니다."""
    preamble, sections = _split_name_sections(text)
    seen_hanja: set[str] = set()
    valid: list[str] = []
    # 이름 헤더에서 한자 추출: ## [이름 N] 이름 (한자조합)
    hanja_pattern = re.compile(r'##\s*\[이름\s*\d+\]\s*[가-힣]+\s*\(([一-鿿]+)\)')
    for sec in sections:
        m = hanja_pattern.search(sec)
        if not m:
            valid.append(sec)
            continue
        hanja_combo = m.group(1)
        chars = list(hanja_combo)
        if any(ch in seen_hanja for ch in chars):
            continue  # 이미 사용된 한자 포함 → 제거
        seen_hanja.update(chars)
        valid.append(sec)
    if len(valid) == len(sections):
        return text
    if not valid:
        return text
    renumbered = []
    for i, sec in enumerate(valid, 1):
        sec = re.sub(r'(## \[이름 )\d+(\])', rf'\g<1>{i}\2', sec, count=1)
        renumbered.append(sec)
    return preamble + "".join(renumbered)


def _drop_surname_sound_overlap(text: str, surname_kr: str) -> str:
    """이름 마지막 글자 발음이 성씨와 같은 이름을 제거하고 재번호화합니다.
    예: 성씨 '윤'이면 '윤아윤(~윤)' 제거."""
    if not surname_kr:
        return text
    preamble, sections = _split_name_sections(text)
    header_pattern = re.compile(r'##\s*\[이름\s*\d+\]\s*([가-힣]{2,5})')
    valid: list[str] = []
    for sec in sections:
        m = header_pattern.search(sec)
        if not m:
            valid.append(sec)
            continue
        full_name = m.group(1)
        last_sound = full_name[-1]  # 이름 마지막 글자
        if last_sound != surname_kr:
            valid.append(sec)
    if len(valid) == len(sections):
        return text
    if not valid:
        return text
    renumbered = []
    for i, sec in enumerate(valid, 1):
        sec = re.sub(r'(## \[이름 )\d+(\])', rf'\g<1>{i}\2', sec, count=1)
        renumbered.append(sec)
    return preamble + "".join(renumbered)


def _repair_sanggeuk_names(
    text: str, surname_ohaeng: str, context: str, query: str
) -> str:
    """상극 이름의 상극 유발 한자를 동음 다른 오행 한자로 교체합니다.
    교체 후보가 없으면 해당 이름을 제거합니다."""
    if '— 상극' not in text:
        return text

    all_hanja = rag_server._load_person_name_hanja()
    h2r: dict[str, str] = {
        meta.get("hanja", ""): meta.get("hangul", "")
        for _, meta in all_hanja if meta
    }

    prohibit_lines: list[str] = []
    candidate_lines: list[str] = []
    has_any_candidate = False

    parts = re.split(r'(?=\n## \[이름 \d+\])', text)
    for sec in parts:
        if '## [이름' not in sec or '— 상극' not in sec:
            continue

        # 이름 번호 / 한자 목록
        idx_m = re.search(r'## \[이름 (\d+)\]', sec)
        if not idx_m:
            continue
        name_idx = idx_m.group(1)

        # 첫째·둘째 글자 한자 + 오행 파싱
        char_data = re.findall(r'글자\(([一-鿿])\)[^\n]+\[([금목화수토])\]', sec)
        if len(char_data) < 2:
            continue

        first_hanja, first_oe = char_data[0]
        second_hanja, second_oe = char_data[1]
        first_hangul = h2r.get(first_hanja, "")
        second_hangul = h2r.get(second_hanja, "")

        # 어느 전환이 상극인지 파악하고 교체 방향 결정
        # 전략: 둘째 교체 먼저 시도 (첫째는 뜻 조합에 더 영향)
        replaced = False
        for target_char, target_hangul, target_fix_oe_fn in [
            # (교체 대상 한자, 발음, 필요 오행 계산)
            (second_hanja, second_hangul, lambda: [
                o for o in ("화", "토", "금", "수", "목")
                if _pair_relation(first_oe, o) != "상극"
                and _pair_relation(surname_ohaeng, first_oe) != "상극"
            ]),
            (first_hanja, first_hangul, lambda: [
                o for o in ("화", "토", "금", "수", "목")
                if _pair_relation(surname_ohaeng, o) != "상극"
                and _pair_relation(o, second_oe) != "상극"
            ]),
        ]:
            if not target_hangul:
                continue
            for target_oe in target_fix_oe_fn():
                cands = _get_hanja_for_sound(target_hangul, target_oe)
                # 원래 한자 제외
                cands = [m for m in cands if m.get("hanja") != target_char]
                if cands:
                    cand_str = ", ".join(
                        f"{m['hanja']}({m['hangul']}/{m.get('sound_meaning','?')}) [{target_oe}오행]"
                        for m in cands[:5]
                    )
                    prohibit_lines.append(f"[이름 {name_idx}] {target_char}({target_hangul}): 상극 유발 금지")
                    candidate_lines.append(f"[이름 {name_idx}] 대체 후보: {cand_str}")
                    has_any_candidate = True
                    replaced = True
                    break
            if replaced:
                break

    if not has_any_candidate:
        return _drop_sanggeuk_names(text)

    feedback = "\n[교체 금지 한자]\n" + "\n".join(prohibit_lines)
    feedback += "\n[대체 후보]\n" + "\n".join(candidate_lines)

    resp = _llm_generate.invoke([
        SystemMessage(content=_REPAIR_SYSTEM),
        HumanMessage(content=(
            f"[원본 추천 결과]\n{text}\n\n"
            f"[참고 정보]\n{context}\n"
            f"{feedback}\n\n"
            f"[질문]\n{query}"
        )),
    ])
    raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
    raw = re.sub(r'^\[원본 추천 결과\]\s*\n?', '', raw).strip()
    raw = _correct_strokes_in_output(raw)
    raw = _correct_ohaeng_in_output(raw)
    # 수리 후에도 상극 남아있으면 제거
    if '— 상극' in raw:
        raw = _drop_sanggeuk_names(raw)
    return raw


_hanja_stroke_cache: dict[str, int] | None = None


def _get_hanja_stroke_dict() -> dict[str, int]:
    """인명용 한자 캐시에서 한자→획수 딕셔너리를 반환합니다 (lazy 초기화)."""
    global _hanja_stroke_cache
    if _hanja_stroke_cache is None:
        try:
            all_hanja = rag_server._load_person_name_hanja()
            _hanja_stroke_cache = {
                meta.get("hanja", ""): meta.get("strokes", 0)
                for _, meta in all_hanja
                if meta and meta.get("hanja") and meta.get("strokes")
            }
        except Exception:
            _hanja_stroke_cache = {}
    return _hanja_stroke_cache


def _correct_strokes_in_output(text: str) -> str:
    """LLM 출력에서 획수를 ChromaDB 실제값으로 교정합니다.
    '글자(한자)' 패턴이 있는 줄의 N획을 DB 값으로 대체합니다."""
    stroke_dict = _get_hanja_stroke_dict()
    if not stroke_dict:
        return text
    lines = text.split('\n')
    for i, line in enumerate(lines):
        m = re.search(r'글자\(([一-鿿])\)', line)
        if m:
            correct = stroke_dict.get(m.group(1))
            if correct:
                lines[i] = re.sub(r'\d+획', f'{correct}획', line)
    return '\n'.join(lines)


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
    model="gpt-5.4-mini",
    temperature=0,
    max_tokens=1024,
)

# Generate: 이름 추천 최종 답변 생성
_llm_generate = ChatOpenAI(
    model="gpt-5.4-mini",
    temperature=0.7,
    max_tokens=8192,
)

# Verify: 검증은 결정론적이어야 함 (temperature=0)
_llm_verify = ChatOpenAI(
    model="gpt-5.4-mini",
    temperature=0,
    max_tokens=2048,
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
    - AND/OR 한자 이름 요청인데 성씨가 '○씨' 형태이지만 한자가 괄호 안에 없는 경우 (동음이의 성씨 존재)
  ※ 순우리말 이름 요청은 성씨 한자 불필요 → clarify 조건 미적용
  ※ "남녀 N개씩", "남자 N개 여자 N개" 등은 성별 양쪽 모두 지정한 것 → clarify 금지
  ※ 이름 유형 미지정 시 한자 이름으로 간주하고 hanja_col + paper_col 검색

[규칙]
- JSON만 출력하세요. 다른 텍스트 금지.
- internal_rag 선택 시 collections 배열 필수.
  · 한자 이름 추천(또는 유형 미지정): 반드시 ["hanja_col", "paper_col"] 함께 포함.
  · 순우리말 이름 추천: ["urimalsam_col"] 선택. hanja_col·paper_col 불필요.
  · 한자 + 순우리말 동시 추천: ["hanja_col", "paper_col", "urimalsam_col"] 모두 포함.
- 이미 실행한 Tool은 선택 금지.
- 한자 이름 추천 요청인 경우: [지금까지 수집된 정보 요약]이 '없음'이면 반드시 internal_rag를 먼저 실행. generate 바로 선택 금지.
- 吉수(획수 조합)가 필요한 이름 추천 요청인 경우: sql_db를 반드시 실행해야 한다. internal_rag 실행 후에도 sql_db 결과가 없으면 sql_db를 선택할 것. generate 직행 금지.
- 그 외 정보가 충분하면 generate 선택.

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
    query = state["query"]

    # 성씨 한자는 모든 이터레이션에서 항상 파악 — override 체크(line ~619)도 이 값을 사용
    surname_kr, surname_info = _resolve_surname(query)

    # 첫 진입 시 성씨 한자 확인 — LLM 판단에 맡기지 않고 코드로 강제
    if iterations == 0:
        # 동음이의 없는 성씨는 _SURNAME_OHAENG 사전으로 자동 해결 (clarify 불필요)
        if surname_kr and not surname_info:
            _entries = [(k, v) for k, v in _SURNAME_OHAENG.items() if v.get("hangul") == surname_kr]
            if len(_entries) == 1:
                _hk, _ev = _entries[0]
                surname_info = {"hanja": _hk, "resource_ohaeng": _ev.get("resource_ohaeng", ""), "strokes": 0}
        is_pure_korean = any(kw in query for kw in _KOREAN_NAME_KW) and "한자" not in query
        is_name_req = any(kw in query for kw in {"추천", "작명", "짓", "골라", "제안", "뽑아", "지어", "선정"})
        if surname_kr and not surname_info and not is_pure_korean and is_name_req:
            # 오행이 명시된 경우(火오행 등)는 성씨 한자 없어도 generate 가능
            if not _extract_query_ohaeng(query):
                return {**state, "next_action": "clarify", "iterations": 1}

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

    # 성씨 한자가 이미 확인됐는데 LLM이 clarify를 선택하면 → internal_rag로 교정
    # (LLM은 괄호 없는 한자 표기를 보고 clarify 조건으로 오판하는 경향이 있음)
    if next_action == "clarify" and surname_info and surname_info.get("hanja"):
        next_action = "internal_rag"
        collections = ["hanja_col", "paper_col"]

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
_FEMALE_KW = {"딸", "여아", "여자아이", "여자 아이", "여자이름", "여자 이름", "여자", "여성"}
_MALE_KW = {"아들", "남아", "남자아이", "남자 아이", "남자이름", "남자 이름", "남자", "남성"}


def _build_paper_query(query: str) -> str:
    """이름 추천 요청에서 성별을 감지해 paper_col 전용 검색 쿼리를 생성합니다.
    '빈도표' 포함 → _parse_paper_conditions이 chunk_type=table 필터 적용.
    """
    if any(kw in query for kw in _FEMALE_KW):
        gender = "여성"
    elif any(kw in query for kw in _MALE_KW):
        gender = "남성"
    else:
        gender = "남녀"
    return f"최근 2010~2020년대 {gender} 이름 선호 경향 트렌드 특징 받침 음절 패턴"


def _sample_hanja_by_sangsaeng(query: str, surname_ohaeng: str, n_results: int) -> str:
    """성씨 오행 기준 상생 체인 오행으로 시맨틱 검색합니다.
    오행 필터 + 원본 쿼리 의미 검색을 결합해 글자 풀을 구성합니다.
    Neo4j 조회 실패 시 오행 필터 없이 시맨틱 검색으로 fallback."""
    per_col = min(n_results // 2, 30)

    try:
        rel1 = graph_server.get_ohaeng_relations(surname_ohaeng)
        m1 = re.search(r'상생\(GENERATES\):\s*([가-힣]+)', rel1)
        if not m1:
            return rag_server.search_rag(query, "hanja_col", n_results=min(n_results, 30))
        next1 = m1.group(1)

        rel2 = graph_server.get_ohaeng_relations(next1)
        m2 = re.search(r'상생\(GENERATES\):\s*([가-힣]+)', rel2)
        next2 = m2.group(1) if m2 else ""
    except Exception:
        return rag_server.search_rag(query, "hanja_col", n_results=min(n_results, 30))

    is_single = any(kw in query for kw in _SINGLE_KW)
    if is_single:
        return rag_server.search_rag(f"{next1}오행 {query}", "hanja_col", n_results=min(n_results, 30))

    part1 = rag_server.search_rag(f"{next1}오행 {query}", "hanja_col", n_results=per_col)
    part2 = rag_server.search_rag(
        f"{next2}오행 {query}" if next2 else f"{next1}오행 {query}",
        "hanja_col", n_results=per_col,
    )
    return part1 + "\n\n" + part2


def internal_rag_node(state: NamingState) -> NamingState:
    """LLM이 선택한 컬렉션을 ChromaDB에서 검색합니다."""
    query = state["query"]
    collections = list(state.get("collections") or ["hanja_col"])  # 폴백: hanja_col

    is_name_query = any(kw in query for kw in _NAME_RECOMMEND_KW)

    # 이름 추천 요청 시 paper_col 무조건 포함
    if is_name_query and "paper_col" not in collections:
        collections = ["paper_col"] + collections

    # paper_col을 먼저 처리해서 트렌드 정보를 컨텍스트 앞부분에 배치
    ordered = sorted(collections, key=lambda c: 0 if c == "paper_col" else 1)

    results = []
    for col in ordered:
        if col == "paper_col" and is_name_query:
            paper_query = _build_paper_query(query)
            results.append(rag_server.search_rag(paper_query, col, n_results=10))
        elif col == "hanja_col" and is_name_query:
            count_match = re.search(r'(\d+)\s*개', query)
            req_count = int(count_match.group(1)) if count_match else 3
            n_results = min(max(50, req_count * 8), 80)
            # 성씨 오행이 확인되면 상생 체인 오행으로 분할 샘플링
            _, surname_info = _resolve_surname(query)
            if surname_info and surname_info.get("resource_ohaeng"):
                results.append(_sample_hanja_by_sangsaeng(
                    query, surname_info["resource_ohaeng"], n_results
                ))
            else:
                results.append(rag_server.sample_hanja(query, n_results=n_results))
        elif col == "urimalsam_col" and is_name_query:
            count_match = re.search(r'(\d+)\s*개', query)
            req_count = int(count_match.group(1)) if count_match else 3
            n_results = min(max(30, req_count * 6), 60)
            is_single_req = any(kw in query for kw in _SINGLE_KW)
            results.append(rag_server.sample_urimalsam(query, n_results=n_results, single_only=is_single_req))
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

    nums = re.findall(r'\d+', query)  # \b\d+\b는 한글 앞에서 미매칭 — \d+로 대체

    if any(kw in query for kw in {"어울리는 획수", "吉수", "획수 조합", "역산", "길한 획수"}):
        if nums:
            result = _parse_db_result(db_server.find_lucky_strokes(int(nums[0])))
        else:
            result = "[안내] 성씨 한자를 괄호로 표기해 주세요. 예: '김씨(金) 성에 어울리는 吉수 조합'"

    elif any(kw in query for kw in {"오행 조합", "오행 궁합"}):
        ohaeng = re.findall(r"[木火土金水]", query)
        if len(ohaeng) >= 3:
            result = _parse_db_result(db_server.lookup_ohaeng_combo(ohaeng[0], ohaeng[1], ohaeng[2]))
        else:
            result = "[안내] 오행 조합 조회는 성씨·이름1·이름2의 오행(木/火/土/金/水)을 모두 입력해주세요."

    elif len(nums) >= 3:
        calc = _parse_db_result(db_server.calculate_name_suri(int(nums[0]), int(nums[1]), int(nums[2])))
        result = "[수리 계산 완료] 아래 4격 수치는 이미 계산된 값입니다. 반드시 그대로 사용하고 직접 재계산 금지.\n\n" + calc

    elif len(nums) == 1:
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

    # 성씨 한자 필요 여부 판단 — 한자 이름 요청일 때만 반문
    surname_kr, surname_info = _resolve_surname(query)
    is_pure_korean_req = any(kw in query for kw in _KOREAN_NAME_KW) and "한자" not in query
    needs_surname_hanja = bool(surname_kr and not surname_info and not is_pure_korean_req)

    questions = []
    if not has_gender:
        questions.append("**성별이 어떻게 되나요?** (아들 / 딸)")
    if not has_type:
        questions.append("**어떤 종류의 이름을 원하시나요?** (한자 이름 / 순우리말 이름)")
    if needs_surname_hanja:
        questions.append(
            f"**'{surname_kr}씨' 성씨의 한자가 어떻게 되나요?**\n"
            f"   한국에는 동음이의 성씨가 있어 정확한 한자 확인이 필요합니다.\n"
            f"   한자 문자만 답변해 주시면 됩니다. (예: 林 또는 任)\n"
            f"   한자를 모르신다면 네이버 한자사전에서 성씨를 검색해 주세요."
        )

    if not questions:
        # 반문 조건 없음 → 바로 generate로 위임 (안전망)
        questions.append("**추가로 알려주실 정보가 있으신가요?** (성별, 원하는 뜻, 발음 등)")

    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))

    answer = (
        f"좋은 이름을 추천해드리기 위해 아래 정보를 알려주세요.\n\n"
        f"{numbered}\n\n"
        f"추가로 원하는 뜻·느낌(예: 밝은, 지혜로운, 강한)이나 선호하는 발음이 있으면 함께 알려주세요."
    )
    return {**state, "answer": answer}


# ─────────────────────────────────────────────
# 답변 생성 노드
# ─────────────────────────────────────────────

_GENERATE_SYSTEM_SINGLE = """당신은 한국 작명 전문가 AI입니다. 외자(성씨 포함 총 2자) 이름을 추천하세요.

[한자 선택 규칙 — 엄수]
- [검증된 한자 조합 후보] 제공 시: 반드시 그 후보에서만 선택. 후보 외 한자 절대 금지.
- 후보 없을 시: [hanja_col 결과]에서만 선택. 목록 외 한자·오행 추측 금지.
- hangul 필드 = 해당 한자의 이름 발음. 다른 발음 사용 금지.
- 성씨 한자 재사용 금지. 부정적·불길한 뜻(그치다·막다·슬프다 등) 금지.
- 추천 이름들끼리 같은 한자 절대 중복 금지. 예: 이름 1에 志를 쓰면 이름 2~N에 志 재사용 불가. 이름 전체를 다 생성한 뒤 중복 한자가 있으면 반드시 교체할 것.

[이름 구성 — 우선순위 순]
① 이름으로 아름다운 뜻 우선.
   권장: 빛날·아름다울·고울·맑을·지혜·은혜·꽃·별·봄·기쁠·예쁠·슬기·향기·상서로울
   금지: 집·건물·관청·차례·달력·가지·관직·직업·숫자·사물 (이름으로 어색)
② 오행: 상생 > 중립. 상극 절대 금지.

[출력 형식] — 이름마다 반복:
## [이름 N] {성씨한글}{이름한글자} ({한자1글자})
예: ## [이름 1] 임서 (瑞)   ← 헤더=한글 발음만, 괄호=이름 한자만 (성씨 한자 금지)
**추천 이유**: 의미와 오행 조화 1~2문장.
**한자 풀이**:
- 이름 글자({한자}) — {뜻}, {N}획 [{오행}]
**오행 흐름**: 성씨({오행}) → 이름({오행}) — {상생/중립}
수리(획수 합산)를 직접 계산하거나 출력하지 말 것. 수리는 시스템이 자동 삽입한다.
[吉수 한자 획수 조건] 표시가 있으면 해당 획수 조합의 한자를 우선 선택.
수리 후처리로 일부 이름이 제거될 수 있으니 요청보다 넉넉하게 생성하되, 응답 첫 줄에 생성 개수를 언급하지 마세요 — 이름 추천만 출력합니다.

[논문: ...], [한자: ...], [출처: ...], [참고 정보], [검증된 한자 조합 후보], [吉수리 획수 힌트] 등 내부 태그·컨텍스트 출력 절대 금지. 이름 추천 결과만 출력. 답변은 한국어."""


_GENERATE_SYSTEM_KOREAN = """당신은 한국 작명 전문가 AI입니다.

[제약 — 엄수]
- 기본적으로 [urimalsam_col 결과] 목록의 단어를 사용하여 이름을 추천하세요.
- 단, 사용자가 특정 순우리말(예: 가람, 나래 등)을 명시적으로 질문하거나 추천을 요구한 경우에는, 해당 단어가 [urimalsam_col 결과]에 없더라도 거절하지 말고 이름 후보로 적극 검토하고 뜻풀이를 제공하세요.
- 한자를 한글로 읽은 단어도 한자어 → 금지.
- 이름으로 어색한 단어(동사·형용사 어간, 행동·현상 표현) 금지.
- 동음이의어에 부정적 의미가 있는 단어 금지. 예: 아슬(아슬아슬), 나락(나락에 빠지다), 독(독이 되다).

[출력 형식] — 이름마다 반복:
## [이름 N] {성씨}{순우리말이름}
**추천 이유**: 추천 근거 1~2문장.
**뜻풀이**: [urimalsam_col 결과]에 기재된 해당 단어의 뜻을 그대로 사용. 재해석·추가 설명 금지.

획수·오행·한자 언급 절대 금지. 답변은 한국어."""

_GENERATE_SYSTEM_KOREAN_SINGLE = """당신은 한국 작명 전문가 AI입니다.

[제약 — 엄수]
- [urimalsam_col 결과] 목록에서 반드시 1음절(한 글자) 단어만 선택. 2글자 이상 절대 금지.
- 목록 외 단어·한자어·임의 조합 절대 금지.
- 한자를 한글로 읽은 단어도 한자어 → 금지.
- 이름으로 어색한 단어(동사·형용사 어간, 행동·현상 표현) 금지.
- 동음이의어에 부정적 의미가 있는 단어 금지. 예: 독(독약), 날(날이 서다), 불(불이 나다).

[출력 형식] — 이름마다 반복:
## [이름 N] {성씨}{순우리말1글자}
**추천 이유**: 추천 근거 1~2문장.
**뜻풀이**: [urimalsam_col 결과]에 기재된 해당 단어의 뜻을 그대로 사용. 재해석·추가 설명 금지.

획수·오행·한자 언급 절대 금지. 답변은 한국어."""



_GENERATE_SYSTEM = """당신은 한국 작명 전문가 AI입니다.

[한자 선택 규칙 — 엄수]
- 한자는 [검증된 한자 조합 후보] 또는 [hanja_col 결과]에서만 선택. 목록 외 한자·오행 추측 금지.
- [검증된 한자 조합 후보]는 오행이 검증된 후보 예시다. 뜻이 더 아름다운 한자가 hanja_col에 있으면 hanja_col 선택 가능.
- hangul 필드 = 해당 한자의 이름 발음. 다른 발음 사용 금지.
- 성씨 한자를 이름에 재사용 금지. 성씨와 이름 첫 글자 또는 마지막 글자 발음 동일 금지.
- 추천 이름끼리 한글 발음이 같으면 안 됨. 예: '서연'이 이미 있으면 한자가 달라도 '서연' 다시 금지.
- 추천 이름들끼리 같은 한자 절대 중복 금지. 예: 이름 1에 志를 쓰면 이름 2~N에 志 재사용 불가. 이름 전체를 다 생성한 뒤 중복 한자가 있으면 반드시 교체할 것.

[이름 구성 — 우선순위 순]
① 두 한자의 뜻이 조합되어 자연스러운 하나의 이미지를 만드는 조합 선택.
   好: 智(지혜)+雅(우아할), 柔(부드러울)+恩(은혜), 瑞(상서로울)+蓮(연꽃)
   不: 書(글)+然(그럴) 연결 어색 | 廈(큰집)+恩(은혜) 의미 단절
② 이름으로 아름다운 뜻 우선.
   권장: 빛날·아름다울·고울·맑을·지혜·은혜·꽃·별·봄·기쁠·예쁠·슬기·향기·상서로울
   금지: 걸을·갚을·작은성·달릴·떨어질·관청·관직·직업·숫자·사물·현상 (이름으로 어색)
③ 오행: 상생 > 중립. 상극 절대 금지. 뜻이 더 좋으면 중립을 상생보다 우선 가능.

[출력 형식] — 이름마다 반복:
## [이름 N] {성씨한글}{이름한글} ({이름한자1}{이름한자2})
예: ## [이름 1] 임지아 (智雅)   ← 헤더=한글 발음만, 괄호=이름 한자만 (성씨 한자 금지)
**추천 이유**: 두 한자 뜻의 결합 이미지 1~2문장.
**한자 풀이**:
- 첫째 글자({한자1}) — {뜻}, {N}획 [{오행}]
- 둘째 글자({한자2}) — {뜻}, {N}획 [{오행}]
**오행 흐름**: 성씨({오행}) → 첫째({오행}) → 둘째({오행}) — {상생/중립}
수리(획수 합산)를 직접 계산하거나 출력하지 말 것. 수리는 시스템이 자동 삽입한다.
[吉수 한자 획수 조건] 표시가 있으면 해당 획수 조합의 한자를 우선 선택.
수리 후처리로 일부 이름이 제거될 수 있으니 요청보다 넉넉하게 생성하되, 응답 첫 줄에 생성 개수를 언급하지 마세요 — 이름 추천만 출력합니다.

[논문: ...], [한자: ...], [출처: ...], [참고 정보], [검증된 한자 조합 후보], [吉수리 획수 힌트] 등 내부 태그·컨텍스트 출력 절대 금지. 이름 추천 결과만 출력. 답변은 한국어."""


# ─────────────────────────────────────────────
# 이름 검증 / 수리
# ─────────────────────────────────────────────

_VERIFY_SYSTEM = """이름 추천 결과에서 이름으로 부적절한 한자를 찾으세요.

부적절 기준:
- 부정적·불길한 의미: 죽을·어두울·슬플·그칠·막힐·쓸쓸할·괴로울 등
- 미성숙·유아 의미: 어릴·젖먹이·아기·아이 등
- 이름으로 어색한 의미: 관청·관직·직업명·구체적 사물·동작·현상 표현 등

출력 형식 (각 문제 한 줄):
- 문제 없으면: 이상 없음
- 문제 있으면: [이름 N] 한자(발음): 이유"""

_REPAIR_SYSTEM = """이름 추천 결과에서 지정된 한자를 교체하세요.

규칙:
1. [교체 금지 한자] 목록의 한자 절대 사용 금지.
2. [대체 후보]에서 같은 발음이고 뜻이 긍정적인 한자 선택.
3. 교체된 이름의 제목·한자 풀이·추천 이유를 새 한자에 맞게 수정.
4. 교체하지 않는 이름은 원문 그대로.
5. 출력 형식은 원본과 동일."""

_VERIFY_SYSTEM_KOREAN = """순우리말 이름 추천 결과에서 이름으로 부적절한 단어를 찾으세요.

성씨가 함께 제공되면 성씨+이름 전체 조합도 검토하세요.

[부적절 기준]

1. 동음이의어 — 소리가 같거나 유사한 단어 중 부정적 의미가 일상에서 더 강하게 쓰이는 경우:
   - 고통·불운: 나락(나락에 빠지다), 독(독약·독이 되다), 아픔, 눈물, 한(恨)
   - 위험·불안: 아슬(아슬아슬), 벼랑, 날(날이 서다·칼날), 여진(지진의 여진)
   - 불투명·소멸: 안개(앞이 안 보임), 재(재가 되다), 불(화재 연상)
   - 단, 긍정 의미가 명확히 우세한 단어는 허용. 예: 별(희망), 달(풍요), 솔(소나무), 봄(계절)

2. 동사·형용사 어간: 이름이 행동·상태 표현으로 읽히는 것
   예: 안다(알다), 진다(지다), 빛나(빛나다), 깊다, 푸르다

3. 성씨+이름 합성 문제: 붙였을 때 어색한 문장·속어·부정적 표현이 되는 경우
   예: 성씨 "임" + 이름 "신" → "임신"

판단 기준: 일반인이 이름을 들었을 때 부정적 연상이 먼저 떠오르면 부적합.

출력 형식 (각 문제 한 줄):
- 문제 없으면: 이상 없음
- 문제 있으면: [이름 N] 이름단어: 이유"""

_REPAIR_SYSTEM_KOREAN = """순우리말 이름 추천 결과에서 지정된 이름을 교체하세요.

규칙:
1. [교체 금지 단어] 목록의 단어는 절대 사용 금지.
2. [참고 정보]의 urimalsam_col 목록에서 뜻이 긍정적이고 이름으로 자연스러운 단어를 선택.
3. 교체된 이름의 추천 이유와 뜻풀이를 새 단어에 맞게 수정.
4. 교체하지 않는 이름은 원문 그대로 유지.
5. 출력 형식은 원본과 동일하게 유지."""


def _verify_names(answer: str) -> list[dict]:
    """이름 추천 결과를 검증하여 문제 한자 목록을 반환합니다."""
    resp = _llm_verify.invoke([
        SystemMessage(content=_VERIFY_SYSTEM),
        HumanMessage(content=f"[추천 결과]\n{answer}"),
    ])
    raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
    if "이상 없음" in raw:
        return []
    issues = []
    for m in re.finditer(r'\[이름\s*(\d+)\]\s*([一-鿿]+)\(([가-힣]+)\)', raw):
        issues.append({"name_idx": m.group(1), "hanja": m.group(2), "hangul": m.group(3)})
    return issues


def _verify_names_korean(answer: str, surname_kr: str) -> list[dict]:
    """순우리말 이름 추천 결과를 검증하여 문제 단어 목록을 반환합니다."""
    resp = _llm_verify.invoke([
        SystemMessage(content=_VERIFY_SYSTEM_KOREAN),
        HumanMessage(content=f"[성씨]: {surname_kr}씨\n[추천 결과]\n{answer}"),
    ])
    raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
    if "이상 없음" in raw:
        return []
    issues = []
    for m in re.finditer(r'\[이름\s*(\d+)\]\s*([가-힣]+):', raw):
        issues.append({"name_idx": m.group(1), "name": m.group(2)})
    return issues


def _repair_names_korean(
    answer: str, issues: list[dict], context_with_surname: str, query: str
) -> str:
    """문제 순우리말 단어를 urimalsam_col 목록에서 교체합니다."""
    prohibit_lines = [f"- {issue['name']}" for issue in issues]
    feedback = "\n[교체 금지 단어]\n" + "\n".join(prohibit_lines)
    repair_content = (
        f"[원본 추천 결과]\n{answer}\n\n"
        f"[참고 정보]\n{context_with_surname}\n"
        f"{feedback}\n\n"
        f"[질문]\n{query}"
    )
    resp = _llm_generate.invoke([
        SystemMessage(content=_REPAIR_SYSTEM_KOREAN),
        HumanMessage(content=repair_content),
    ])
    raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
    return re.sub(r'^\[원본 추천 결과\]\s*\n?', '', raw).strip()


def _find_same_reading_candidates(hangul: str, exclude_hanja: str) -> list[dict]:
    """같은 발음(hangul)의 인명용 한자 후보 목록을 반환합니다."""
    all_hanja = rag_server._load_person_name_hanja()
    return [
        meta for _, meta in all_hanja
        if meta and meta.get("hangul") == hangul and meta.get("hanja") != exclude_hanja
    ]


def _repair_names(
    answer: str, issues: list[dict], system: str, context_with_surname: str, query: str
) -> str:
    """문제 한자를 동음 후보로 교체하여 해당 이름을 재생성합니다."""
    prohibit_lines = []
    candidate_lines = []

    for issue in issues:
        candidates = _find_same_reading_candidates(issue["hangul"], issue["hanja"])[:10]
        prohibit_lines.append(f"- {issue['hanja']}({issue['hangul']}): 부적절, 사용 금지")
        if candidates:
            cands = ", ".join(
                f"{m.get('hanja')}({m.get('hangul')}/{m.get('sound_meaning', '?')}) [{m.get('resource_ohaeng', '?')}오행]"
                for m in candidates
            )
            candidate_lines.append(f"  '{issue['hangul']}' 대체 후보: {cands}")

    feedback = "\n[교체 금지 한자]\n" + "\n".join(prohibit_lines)
    if candidate_lines:
        feedback += "\n[대체 후보]\n" + "\n".join(candidate_lines)

    repair_content = (
        f"[원본 추천 결과]\n{answer}\n\n"
        f"[참고 정보]\n{context_with_surname}\n"
        f"{feedback}\n\n"
        f"[질문]\n{query}"
    )
    resp = _llm_generate.invoke([
        SystemMessage(content=_REPAIR_SYSTEM),
        HumanMessage(content=repair_content),
    ])
    raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
    raw = re.sub(r'^\[원본 추천 결과\]\s*\n?', '', raw).strip()
    raw = _correct_strokes_in_output(raw)
    return _correct_ohaeng_in_output(raw)


# ─────────────────────────────────────────────
# 음절 후보 생성 + 한자 exact 매핑
# ─────────────────────────────────────────────

_SOUND_GEN_SYSTEM = """이름 발음 후보(음절 조합)를 JSON 배열로만 출력하세요. 한자는 선택하지 않습니다.

[참고 정보]의 paper_col 논문 트렌드(최근 선호 음절·받침 패턴)와 성씨·성별을 반영하세요.
성씨 발음과 이름 첫 글자 발음이 동일하면 안 됩니다.
발음은 한글 1음절 단위로 분리합니다.
첫 글자 발음이 중복되지 않도록 다양하게 생성하세요. 예: "지"가 이미 있으면 "지"로 시작하는 조합 추가 금지.

출력 형식 (JSON 배열만, 다른 텍스트 절대 금지):
2글자 예시: [["서","연"],["지","아"],["하","은"],["유","나"],["소","율"],["채","원"],["나","은"],["예","린"],["민","서"],["아","름"],["보","라"],["다","은"],["혜","원"],["수","아"],["미","래"],["도","연"],["가","은"],["라","온"],["세","아"],["빛","나"]]
1글자 예시: [["서"],["하"],["윤"],["아"],["나"],["채"],["율"],["은"],["린"],["빛"],["솔"],["결"],["별"],["봄"],["빈"],["담"],["다"]]"""


def _get_hanja_for_sound(hangul: str, resource_ohaeng: str) -> list[dict]:
    """발음 + 자원오행으로 인명용 한자를 조회합니다.
    Neo4j(PERMITTED_BY 법령 검증됨) 우선, 실패 시 ChromaDB fallback."""
    try:
        neo4j_results = graph_server.get_hanja_by_sound_and_ohaeng(hangul, resource_ohaeng, limit=5)
        if neo4j_results:
            return neo4j_results
    except Exception:
        pass
    # ChromaDB fallback
    all_hanja = rag_server._load_person_name_hanja()
    return [
        meta for _, meta in all_hanja
        if meta
        and meta.get("hangul") == hangul
        and meta.get("resource_ohaeng") == resource_ohaeng
    ]


def _generate_sound_candidates(query: str, context: str, is_single: bool) -> list[list[str]]:
    """paper_col 트렌드를 참고해 이름 발음 후보 목록을 생성합니다."""
    size_hint = "1글자(외자)" if is_single else "2글자(두자)"
    resp = _llm_router.invoke([
        SystemMessage(content=_SOUND_GEN_SYSTEM),
        HumanMessage(content=(
            f"[참고 정보]\n{context[:4000]}\n\n"
            f"[질문]\n{query}\n"
            f"({size_hint} 이름 발음 후보 20~25개 생성. 첫 글자 발음이 다양하게 분산되도록 할 것)"
        )),
    ])
    raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
    try:
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            if isinstance(parsed, list) and all(isinstance(x, list) for x in parsed):
                return [
                    [s for s in combo if isinstance(s, str) and re.match(r'^[가-힣]+$', s)]
                    for combo in parsed if combo
                ]
    except Exception:
        pass
    return []


def _build_verified_hanja_pool(
    sound_candidates: list[list[str]],
    surname_info: dict | None,
    is_single: bool,
    surname_kr: str,
) -> str:
    """각 음 조합에 대해 (첫째오행, 둘째오행) 쌍 단위로 검증된 한자 풀을 구성합니다.
    상극이 되는 쌍은 처음부터 제외해 LLM이 상극 조합을 선택할 수 없게 합니다."""
    if not sound_candidates or not surname_info:
        return ""

    surname_ohaeng = surname_info.get("resource_ohaeng", "")
    if not surname_ohaeng:
        return ""

    # 성씨 획수 조회 — 수리 사전 필터에서 정격/원격 계산에 사용
    s_strokes = 0
    if surname_info.get("hanja"):
        try:
            s_strokes = rag_server.get_hanja_strokes(surname_info["hanja"]) or 0
        except Exception:
            pass
        if not s_strokes:
            s_strokes = int(_SURNAME_OHAENG.get(surname_info.get("hanja", ""), {}).get("strokes", 0) or 0)

    try:
        rel1 = graph_server.get_ohaeng_relations(surname_ohaeng)
        m1 = re.search(r'상생\(GENERATES\):\s*([가-힣]+)', rel1)
        sangsaeng1 = m1.group(1) if m1 else ""
        sangsaeng2 = ""
        if not is_single and sangsaeng1:
            rel2 = graph_server.get_ohaeng_relations(sangsaeng1)
            m2 = re.search(r'상생\(GENERATES\):\s*([가-힣]+)', rel2)
            sangsaeng2 = m2.group(1) if m2 else ""
    except Exception:
        return ""

    def fmt(meta: dict) -> str:
        return (
            f"{meta.get('hanja')}({meta.get('hangul')}) "
            f"[뜻:{meta.get('sound_meaning','?')}, "
            f"{meta.get('strokes','?')}획, "
            f"{meta.get('resource_ohaeng','?')}오행]"
        )

    def sangsaeng_of(o: str) -> str:
        """상생 체인에서 o의 다음 오행. 체인에 없으면 전체 SANGSAENG_PAIRS에서 계산."""
        if o == surname_ohaeng:
            return sangsaeng1
        if o == sangsaeng1:
            return sangsaeng2
        for (a, b) in _SANGSAENG_PAIRS:
            if a == o:
                return b
        return ""

    chain_desc = (
        f"성씨({surname_ohaeng})"
        + (f"→상생({sangsaeng1})" if sangsaeng1 else "")
        + (f"→상생({sangsaeng2})" if sangsaeng2 else "")
    )
    lines = [
        f"[검증된 한자 조합 후보] 상생 체인: {chain_desc}",
        "아래 조합만 사용. 후보 외 한자·상극 조합 절대 금지.\n",
    ]
    valid_count = 0

    for sounds in sound_candidates:
        if not sounds:
            continue
        if surname_kr and sounds[0] == surname_kr:
            continue

        if is_single:
            ss = _get_hanja_for_sound(sounds[0], sangsaeng1) if sangsaeng1 else []
            jr = _get_hanja_for_sound(sounds[0], surname_ohaeng)
            if not ss and not jr:
                continue
            valid_count += 1
            parts = []
            if ss:
                parts.append(f"상생[{sangsaeng1}]: " + " / ".join(fmt(m) for m in ss[:3]))
            if jr:
                parts.append(f"중립[{surname_ohaeng}]: " + " / ".join(fmt(m) for m in jr[:2]))
            lines.append(f"• '{sounds[0]}': {' | '.join(parts)}")
        else:
            if len(sounds) < 2:
                continue

            # (첫째오행, 둘째오행) 쌍 열거 — 상극 쌍은 제외
            # 상생/중립(동일) 우선, 이후 비상극 중립 오행도 포함해 후보 풀 확대
            _priority_o1 = [o for o in [sangsaeng1, surname_ohaeng] if o]
            _all_o = ["목", "화", "토", "금", "수"]
            _extra_o1 = [
                o for o in _all_o
                if o not in _priority_o1
                and (surname_ohaeng, o) not in _SANGGEUK_PAIRS
                and (o, surname_ohaeng) not in _SANGGEUK_PAIRS
            ]
            first_ohaengs = _priority_o1 + _extra_o1
            valid_pairs = []

            for o1 in first_ohaengs:
                pool1 = _get_hanja_for_sound(sounds[0], o1)
                if not pool1:
                    continue
                o1_ss = sangsaeng_of(o1)
                second_ohaengs = list(dict.fromkeys(o for o in [o1_ss, o1] if o))
                for o2 in second_ohaengs:
                    if _pair_relation(o1, o2) == "상극":
                        continue  # 상극 쌍 제외
                    pool2 = _get_hanja_for_sound(sounds[1], o2)
                    if not pool2:
                        continue
                    rel = _pair_relation(o1, o2)
                    valid_pairs.append((o1, o2, pool1[:3], pool2[:3], rel))

            if not valid_pairs:
                continue

            valid_count += 1

            pair_lines = []
            for o1, o2, p1, p2, rel in valid_pairs[:4]:
                chars1 = " / ".join(fmt(m) for m in p1)
                chars2 = " / ".join(fmt(m) for m in p2)
                # 수리 품질 주석: 첫 번째 후보 쌍의 정격/원격을 계산해 LLM에 표시
                suri_note = ""
                if s_strokes and p1 and p2:
                    good, bad = [], []
                    for h1 in p1[:2]:
                        st1 = int(h1.get("strokes") or 0)
                        for h2 in p2[:2]:
                            st2 = int(h2.get("strokes") or 0)
                            if not st1 or not st2:
                                continue
                            wr = _suri_rating(st1 + st2)
                            jr = _suri_rating(s_strokes + st1 + st2)
                            label = f"{h1.get('hanja')}{h2.get('hanja')}→정격{s_strokes+st1+st2}{jr}"
                            if jr not in _SURI_BAD and wr != "大凶":
                                good.append(label)
                            else:
                                bad.append(label + "(凶)")
                    parts = ["(O)" + g for g in good] + bad
                    if parts:
                        suri_note = " [수리힌트:" + ", ".join(parts[:3]) + "]"
                pair_lines.append(
                    f"  [{rel}: 성씨({surname_ohaeng})→첫째({o1})→둘째({o2})]{suri_note} "
                    f"{sounds[0]}: {chars1} | {sounds[1]}: {chars2}"
                )
            lines.append(f"• '{sounds[0]}{sounds[1]}':\n" + "\n".join(pair_lines))

        if valid_count >= 20:
            break

    if valid_count == 0:
        return ""

    return "\n".join(lines)


def _get_sangsaeng_chain_hint(ohaeng: str, is_single: bool) -> str:
    """Neo4j에서 성씨 오행의 상생 체인을 조회해 generate 프롬프트 힌트를 반환합니다.
    Neo4j 조회 실패 시 빈 문자열 반환 (힌트 없이 generate 진행)."""
    try:
        rel1 = graph_server.get_ohaeng_relations(ohaeng)
        m1 = re.search(r'상생\(GENERATES\):\s*([가-힣]+)', rel1)
        if not m1:
            return ""
        next1 = m1.group(1)
        if is_single:
            return f"\n→ 전체 상생: 첫째 글자 자원오행={next1}"
        rel2 = graph_server.get_ohaeng_relations(next1)
        m2 = re.search(r'상생\(GENERATES\):\s*([가-힣]+)', rel2)
        next2 = m2.group(1) if m2 else ""
        if next2:
            return f"\n→ 전체 상생: 첫째 글자 자원오행={next1}, 둘째 글자 자원오행={next2}"
        return f"\n→ 전체 상생: 첫째 글자 자원오행={next1}"
    except Exception:
        return ""


def _build_lucky_stroke_hint(context: str, query: str = "") -> str:
    """吉수 역산 결과에서 상위 획수 조합에 해당하는 인명용 한자를 직접 추출합니다."""
    m = re.search(r'\[吉수 조합 역산\][^\[]*', context, re.DOTALL)
    if not m:
        return ""
    combos = re.findall(r'\((\d+)획,\s*(\d+)획\)', m.group())
    if not combos:
        return ""

    ohaeng_filter = _extract_query_ohaeng(query) if query else ""

    all_hanja = rag_server._load_person_name_hanja()
    stroke_map: dict[int, list[dict]] = {}
    for _, meta in all_hanja:
        if not meta:
            continue
        s = meta.get("strokes", 0)
        if not s:
            continue
        if ohaeng_filter and meta.get("resource_ohaeng", "") != ohaeng_filter:
            continue
        stroke_map.setdefault(s, []).append(meta)

    def fmt(meta: dict) -> str:
        return (
            f"{meta.get('hanja')}({meta.get('hangul')}/{meta.get('sound_meaning','?')}) "
            f"[{meta.get('resource_ohaeng','?')}오행,{meta.get('strokes')}획]"
        )

    lines = ["[吉수 획수별 인명용 한자 후보] — 아래 획수 조합 중 하나를 사용하세요."]
    found = 0
    for a_s, b_s in combos[:3]:
        a, b = int(a_s), int(b_s)
        pool_a = stroke_map.get(a, [])[:4]
        pool_b = stroke_map.get(b, [])[:4]
        if not pool_a or not pool_b:
            continue
        lines.append(f"• 첫째{a}획: " + " / ".join(fmt(x) for x in pool_a))
        lines.append(f"  둘째{b}획: " + " / ".join(fmt(x) for x in pool_b))
        found += 1

    if found == 0:
        return ""
    lines.append("위 후보 한자와 획수 조합을 우선 사용하세요.")
    return "\n".join(lines)


def _suri_rating(n: int) -> str:
    """81수리 번호(1~81)에 해당하는 등급 문자열을 반환합니다. 81 초과 시 순환."""
    if n <= 0:
        return "?"
    idx = ((n - 1) % 81) + 1
    return _SURI_RATINGS.get(idx, "?")


_SURI_GOOD = {"吉", "大吉", "半吉"}


def _compute_lucky_strokes(surname_hanja: str, name_len: int = 2) -> str:
    """성씨 획수 기반으로 吉 수리 조합과 실제 인명용 한자 후보를 함께 반환합니다."""
    s_strokes = rag_server.get_hanja_strokes(surname_hanja)
    if not s_strokes:
        s_strokes = int(_SURNAME_OHAENG.get(surname_hanja, {}).get("strokes", 0) or 0)
    if not s_strokes or not _SURI_RATINGS:
        return ""

    # 인명용 한자 획수별 분류 (이름 발음·뜻 포함)
    stroke_map: dict[int, list[str]] = {}
    try:
        all_hanja = rag_server._load_person_name_hanja()
        for _, meta in all_hanja:
            if not meta:
                continue
            st = int(meta.get("strokes") or 0)
            if not st:
                continue
            desc = f"{meta.get('hanja')}({meta.get('hangul')}/{meta.get('sound_meaning','?')})"
            stroke_map.setdefault(st, []).append(desc)
    except Exception:
        pass

    combos: list[tuple[int, int, int]] = []  # (good_count, s1, s2)
    for s1 in range(1, 25):
        if name_len == 1:
            won = s1
            hyung = s_strokes + s1
            g = sum(1 for v in [_suri_rating(won), _suri_rating(hyung)] if v in _SURI_GOOD)
            if g >= 2 and stroke_map.get(s1):
                combos.append((g, s1, 0))
        else:
            for s2 in range(1, 25):
                won = s1 + s2
                hyung = s_strokes + s1
                ig = s_strokes + s2
                jeong = s_strokes + s1 + s2
                g = sum(1 for v in [_suri_rating(won), _suri_rating(hyung), _suri_rating(ig), _suri_rating(jeong)] if v in _SURI_GOOD)
                # 4格 모두 吉계열인 조합만 (또는 3格 이상 吉 + 나머지 凶 아닌 경우)
                bad = sum(1 for v in [_suri_rating(won), _suri_rating(hyung), _suri_rating(ig), _suri_rating(jeong)] if v in {"凶", "大凶"})
                if g >= 3 and bad == 0 and stroke_map.get(s1) and stroke_map.get(s2):
                    combos.append((g, s1, s2))

    # 높은 吉格 수 우선, 같은 格 수이면 낮은 획수(인명에 더 일반적) 우선
    combos.sort(key=lambda x: (-x[0], x[1], x[2]))
    if not combos:
        return ""

    lines = []
    seen_s1: set[int] = set()
    count = 0
    for g, s1, s2 in combos:
        if count >= 8:
            break
        if s1 in seen_s1:
            continue
        seen_s1.add(s1)
        cands1 = " / ".join(stroke_map.get(s1, [])[:4])
        if name_len == 1:
            lines.append(
                f"  {s1}획 → 원격{s1}({_suri_rating(s1)}), 형격{s_strokes+s1}({_suri_rating(s_strokes+s1)}) | 후보: {cands1}"
            )
        else:
            cands2 = " / ".join(stroke_map.get(s2, [])[:4])
            lines.append(
                f"  첫째{s1}획+둘째{s2}획 → 원격{s1+s2}({_suri_rating(s1+s2)}), "
                f"형격{s_strokes+s1}({_suri_rating(s_strokes+s1)}), "
                f"이격{s_strokes+s2}({_suri_rating(s_strokes+s2)}), "
                f"정격{s_strokes+s1+s2}({_suri_rating(s_strokes+s1+s2)}) [{g}/4 吉]\n"
                f"    첫째 후보: {cands1}\n"
                f"    둘째 후보: {cands2}"
            )
        count += 1
    return "\n".join(lines)


def _inject_suri_into_output(text: str, surname_info: dict | None) -> str:
    """LLM 출력의 획수 정보를 파싱하여 수리 4格을 계산·삽입합니다.
    이미 [수리 계산 완료] 헤더로 주입된 경우에는 재계산하지 않습니다."""
    if not surname_info or not surname_info.get("hanja"):
        return text

    surname_hanja = surname_info["hanja"]

    # 성씨 획수: hanja_col → 없으면 _SURNAME_OHAENG → 없으면 건너뜀
    s_strokes = rag_server.get_hanja_strokes(surname_hanja)
    if not s_strokes:
        s_strokes = int(_SURNAME_OHAENG.get(surname_hanja, {}).get("strokes", 0) or 0)
    if not s_strokes:
        return text

    def _compute_and_replace(block: str) -> str:
        # [수리 계산 완료] 블록이 있으면 이미 정확한 값 → 건드리지 않음
        if "[수리 계산 완료]" in block:
            return block

        # 첫째/둘째 글자 획수 파싱
        s1_m = re.search(r'첫째 글자\([^)]+\)[^,\n]+,\s*(\d+)획', block)
        s2_m = re.search(r'둘째 글자\([^)]+\)[^,\n]+,\s*(\d+)획', block)
        single_m = re.search(r'이름 글자\([^)]+\)[^,\n]+,\s*(\d+)획', block)

        if single_m:
            s1 = int(single_m.group(1))
            won   = s1
            hyung = s_strokes + s1
            suri_line = f"**수리**: 원격{won}({_suri_rating(won)}), 형격{hyung}({_suri_rating(hyung)})"
        elif s1_m and s2_m:
            s1, s2 = int(s1_m.group(1)), int(s2_m.group(1))
            won    = s1 + s2
            hyung  = s_strokes + s1
            i_geok = s_strokes + s2
            jeong  = s_strokes + s1 + s2
            suri_line = (
                f"**수리**: 원격{won}({_suri_rating(won)}), "
                f"형격{hyung}({_suri_rating(hyung)}), "
                f"이격{i_geok}({_suri_rating(i_geok)}), "
                f"정격{jeong}({_suri_rating(jeong)})"
            )
        else:
            return block  # 획수 파싱 실패 → 원본 유지

        # 기존 수리 라인 교체 or 오행 흐름 다음에 삽입
        if re.search(r'\*\*수리\*\*:', block):
            block = re.sub(r'\*\*수리\*\*:.*', suri_line, block)
        else:
            block = re.sub(
                r'(\*\*오행 흐름\*\*:[^\n]+)',
                r'\1\n' + suri_line,
                block,
            )
        return block

    # 이름 섹션(## [이름 N] ... 다음 ## 또는 끝) 단위로 처리
    parts = re.split(r'(?=## \[이름 \d+\])', text)
    return "".join(_compute_and_replace(p) if p.startswith("## [이름") else p for p in parts)


_SURI_BAD = {"凶", "大凶"}


def _filter_poor_suri_names(text: str) -> str:
    """중요도 순서(형격>이격>원격>정격)에 따라 大凶 이름 블록을 제거합니다.
    형격·이격·원격이 大凶이면 제거. 정격은 가장 먼저 포기하므로 필터 없음.
    凶(비大凶)은 모든 格에서 허용 — 李씨 등 획수 제약이 있는 성씨 대응."""
    parts = re.split(r'(?=## \[이름 \d+\])', text)
    header = parts[0] if not parts[0].startswith("## [이름") else ""
    blocks = [p for p in parts if p.startswith("## [이름")]

    kept = []
    for block in blocks:
        suri_line = re.search(r'\*\*수리\*\*:(.+)', block)
        if not suri_line:
            kept.append(block)
            continue
        line = suri_line.group(1)
        hyung_m = re.search(r'형격\d+\(([^)]+)\)', line)
        i_m     = re.search(r'이격\d+\(([^)]+)\)', line)
        won_m   = re.search(r'원격\d+\(([^)]+)\)', line)
        hyung_r = hyung_m.group(1) if hyung_m else ""
        i_r     = i_m.group(1)     if i_m     else ""
        won_r   = won_m.group(1)   if won_m   else ""
        # 형격·이격·원격 大凶만 탈락. 정격은 필터하지 않음.
        if hyung_r == "大凶" or i_r == "大凶" or won_r == "大凶":
            continue
        kept.append(block)

    # 이름 번호 재정렬
    renumbered = []
    for i, block in enumerate(kept, 1):
        block = re.sub(r'^## \[이름 \d+\]', f'## [이름 {i}]', block)
        renumbered.append(block)

    return header + "".join(renumbered) if renumbered else text


def generate_node(state: NamingState) -> NamingState:
    """수집된 context를 바탕으로 LLM이 최종 답변을 생성합니다."""
    query = state["query"]

    # 이름 유형 감지
    _korean_signal = (
        any(kw in query for kw in _KOREAN_NAME_KW)
        or "우리말" in query
        or "urimalsam_col" in state.get("context", "")
    )
    _hanja_signal = any(kw in query for kw in {"한자", "한자이름", "한자 이름"})
    is_both = _korean_signal and _hanja_signal
    is_korean = _korean_signal and not _hanja_signal
    is_single = any(kw in query for kw in _SINGLE_KW)
    is_female = any(kw in query for kw in _FEMALE_KW)
    is_male   = any(kw in query for kw in _MALE_KW)

    # 성씨 한자 자동 해결
    surname_kr, surname_info = _resolve_surname(query)
    # 성씨 한자 불명 시 fallback (1순위: 명시 오행 '성에 火오행', 2순위: SURNAME_OHAENG 사전)
    if surname_kr and not surname_info:
        explicit_oe = _extract_surname_ohaeng(query)
        if explicit_oe:
            surname_info = {"hanja": "", "resource_ohaeng": explicit_oe, "strokes": 0}
    if surname_kr and not surname_info:
        _gen_entries = [(k, v) for k, v in _SURNAME_OHAENG.items() if v.get("hangul") == surname_kr]
        if len(_gen_entries) == 1:
            _ghk, _gev = _gen_entries[0]
            surname_info = {"hanja": _ghk, "resource_ohaeng": _gev.get("resource_ohaeng", ""), "strokes": 0}

    surname_context = ""
    if surname_kr and surname_info:
        hanja = surname_info["hanja"]
        ohaeng = surname_info.get("resource_ohaeng", "")
        if ohaeng and not is_korean:
            chain_hint = _get_sangsaeng_chain_hint(ohaeng, is_single)
            forbidden = _FORBIDDEN_NEXT.get(ohaeng, [])
            if forbidden:
                forbidden_str = "·".join(forbidden)
                ohaeng_rule = f"\n[상극 금지] 이름 첫째 글자에 {forbidden_str}오행 한자 절대 금지 (성씨→첫째 상극)"
            else:
                ohaeng_rule = ""
            hanja_label = f"한자: {hanja} " if hanja else ""
            surname_context = (
                f"[성씨 정보] {surname_kr}씨 {hanja_label}(자원오행: {ohaeng})"
                f"{chain_hint}{ohaeng_rule}\n\n"
            )
        elif ohaeng:
            hanja_label = f"한자: {hanja} " if hanja else ""
            surname_context = f"[성씨 정보] {surname_kr}씨 {hanja_label}(자원오행: {ohaeng})\n\n"
        else:
            surname_context = f"[성씨 정보] {surname_kr}씨 한자: {hanja} (오행 미상)\n\n"

    # 성별 지시
    if is_female:
        gender_ctx = "[성별] 딸(여아) 이름 — 예쁠·고울·맑을·빛날·은혜·기쁠 등 부드럽고 여성스러운 뜻의 한자 우선. 강·산·무기·관직 등 남성적 뜻의 한자 지양.\n\n"
    elif is_male:
        gender_ctx = "[성별] 아들(남아) 이름 — 강인하고 씩씩하며 긍정적인 뜻의 한자 우선.\n\n"
    else:
        gender_ctx = ""

    context_with_surname = surname_context + gender_ctx + state["context"]

    # 한자 이름 추천 시: 음절 후보 생성 → 발음+오행 exact 매핑 → 검증된 풀 주입
    if not is_korean and surname_info:
        sound_candidates = _generate_sound_candidates(query, context_with_surname, is_single)
        if sound_candidates:
            verified_pool = _build_verified_hanja_pool(
                sound_candidates, surname_info, is_single, surname_kr
            )
            if verified_pool:
                context_with_surname = verified_pool + "\n\n" + context_with_surname

    # 吉수 획수 조합이 있으면 획수별 인명용 한자 후보 주입 (Case 1 개선)
    lucky_hint = _build_lucky_stroke_hint(state["context"], query)
    if lucky_hint:
        context_with_surname = lucky_hint + "\n\n" + context_with_surname
    elif not is_korean and surname_info and surname_info.get("hanja"):
        # sql_db 미호출 시에도 성씨 획수 기반 吉수 조합 자동 주입
        auto_lucky = _compute_lucky_strokes(surname_info["hanja"], name_len=1 if is_single else 2)
        if auto_lucky:
            context_with_surname = (
                f"[吉수리 획수 힌트 — {surname_info['hanja']}씨({rag_server.get_hanja_strokes(surname_info['hanja']) or int(_SURNAME_OHAENG.get(surname_info['hanja'], {}).get('strokes', 0))}획) 기준]\n"
                f"아래 획수 조합을 우선 사용하면 수리 4格 중 3格 이상이 吉계열입니다:\n"
                f"{auto_lucky}\n"
                f"이름 한자 획수를 위 조합에 맞추되, 뜻이 아름다운 한자를 선택하세요.\n\n"
                + context_with_surname
            )

    human_content = f"[참고 정보]\n{context_with_surname}\n\n[질문]\n{query}"

    def _invoke(system: str) -> str:
        try:
            resp = _llm_generate.invoke([SystemMessage(content=system), HumanMessage(content=human_content)])
            raw = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
            if not raw:  # think 태그 제거 후 빈 경우 원본 그대로 사용
                raw = resp.content.strip()
        except Exception as e:
            return f"[모델 오류: {e}]"
        # LLM이 [참고 정보] 컨텍스트를 응답에 그대로 출력한 경우 제거
        raw = re.sub(r'\n{0,2}\[참고 정보\].*$', '', raw, flags=re.DOTALL).strip()
        raw = _fix_broken_headers(raw)
        # 순우리말 형식 교정: [이름 N] → ## [이름 N] (## 누락 시)
        raw = re.sub(r'(?m)^(?!##)\[이름\s+(\d+)\]', r'## [이름 \1]', raw)
        # 헤더 괄호에서 성씨 한자 제거: (任瑜津) → (瑜津) — 한자가 있을 때만
        if surname_info and surname_info.get("hanja"):
            sh = re.escape(surname_info["hanja"])
            raw = re.sub(rf'\(({sh})([一-鿿]{{1,4}})\)', r'(\2)', raw)
        raw = _fix_hanja_headers(raw, surname_kr, surname_info)
        raw = _correct_strokes_in_output(raw)
        raw = _correct_ohaeng_in_output(raw)
        # 수리 4格 후처리 계산 삽입 (LLM 허수 방지) + 정격/원격 凶인 이름 제거
        if not is_korean:
            raw = _inject_suri_into_output(raw, surname_info)
            raw = _filter_poor_suri_names(raw)
        if surname_info and surname_info.get("resource_ohaeng"):
            raw = _repair_sanggeuk_names(raw, surname_info["resource_ohaeng"], context_with_surname, query)
        # 성씨 오행 미인식(resource_ohaeng 없음) 포함, 모든 경우에 상극 이름 제거
        if '— 상극' in raw:
            raw = _drop_sanggeuk_names(raw)
        raw = _drop_duplicate_sound_names(raw)
        if not is_korean:
            raw = _drop_duplicate_hanja_names(raw)
        raw = _drop_surname_sound_overlap(raw, surname_kr)
        return raw

    def _verify_and_repair(text: str, system: str) -> str:
        issues = _verify_names(text)
        if issues:
            text = _repair_names(text, issues, system, context_with_surname, query)
            # repair 이후 수리 재계산·필터 재적용 (LLM 재생성 시 hallucination 방지)
            if not is_korean:
                text = _inject_suri_into_output(text, surname_info)
                text = _filter_poor_suri_names(text)
        return text

    def _verify_and_repair_korean(text: str) -> str:
        issues = _verify_names_korean(text, surname_kr)
        if issues:
            text = _repair_names_korean(text, issues, context_with_surname, query)
        return text

    def _post_repair(text: str) -> str:
        """_verify_and_repair 이후 LLM 재생성으로 유입된 상극/중복을 제거합니다."""
        if '— 상극' in text:
            text = _drop_sanggeuk_names(text)
        text = _drop_duplicate_sound_names(text)
        text = _drop_surname_sound_overlap(text, surname_kr)
        return text

    if is_both:
        hanja_system = _GENERATE_SYSTEM_SINGLE if is_single else _GENERATE_SYSTEM
        korean_system = _GENERATE_SYSTEM_KOREAN_SINGLE if is_single else _GENERATE_SYSTEM_KOREAN
        hanja_raw = _invoke(hanja_system)
        hanja_clean = re.sub(r'\s*-{3,}\s*⚠️.*$', '', hanja_raw, flags=re.DOTALL).strip()
        hanja_clean = _post_repair(_verify_and_repair(hanja_clean, hanja_system))
        korean_raw = _invoke(korean_system)
        korean_raw = _verify_and_repair_korean(korean_raw)
        korean_raw = _drop_duplicate_sound_names(korean_raw)
        korean_raw = _drop_surname_sound_overlap(korean_raw, surname_kr)
        answer = f"## 한자 이름\n\n{hanja_clean}\n\n## 순우리말 이름\n\n{korean_raw}"
    else:
        if is_korean and is_single:
            system = _GENERATE_SYSTEM_KOREAN_SINGLE
        elif is_korean:
            system = _GENERATE_SYSTEM_KOREAN
        elif is_single:
            system = _GENERATE_SYSTEM_SINGLE
        else:
            system = _GENERATE_SYSTEM

        answer = _invoke(system)
        if is_korean:
            answer = _verify_and_repair_korean(answer)
            answer = _drop_duplicate_sound_names(answer)
            answer = _drop_surname_sound_overlap(answer, surname_kr)
        else:
            answer = _post_repair(_verify_and_repair(answer, system))

    # 요청 개수보다 부족한 경우 안내 메시지 삽입
    _req_m = re.search(r'(\d+)\s*개', query)
    if _req_m:
        _requested = int(_req_m.group(1))
        _actual = len(re.findall(r'## \[이름 \d+\]', answer))
        if 0 < _actual < _requested:
            answer = (
                f"요청하신 {_requested}개 중 수리·오행 조건에 부합하는 이름 {_actual}개를 추천드립니다.\n\n"
                + answer
            )

    disclaimer = "\n\n---\n\n⚠️ 면책 고지: 추천 이름의 출생신고 가능 여부를 100% 보장하지 않습니다. 최종 확인은 관할 기관을 통해 진행하세요."
    if "면책 고지" not in answer:
        answer += disclaimer
    return {**state, "answer": answer}


# ─────────────────────────────────────────────
# 그래프 조립
# ─────────────────────────────────────────────

def build_graph() -> StateGraph:
    # 앱 시작 시 Neo4j에서 오행 관계 로드 (실패해도 Python fallback으로 정상 동작)
    global _SANGSAENG_PAIRS, _SANGGEUK_PAIRS, _FORBIDDEN_NEXT
    try:
        neo4j_ss, neo4j_sg = graph_server.get_ohaeng_pairs()
        if len(neo4j_ss) == 5 and len(neo4j_sg) == 5:
            _SANGSAENG_PAIRS = neo4j_ss
            _SANGGEUK_PAIRS = neo4j_sg
            _FORBIDDEN_NEXT = _rebuild_forbidden_next()
    except Exception:
        pass  # fallback: Python 상수 유지

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
