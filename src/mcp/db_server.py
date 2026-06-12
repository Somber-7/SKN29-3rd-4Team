"""
db_server.py — 수리/오행 연산 MCP 서버

81수리 4격 연산과 오행 조합 운세 조회를 담당합니다.
데이터: data/raw/reference/81suri.json, yinyang.json

[Tool 목록]
  1. get_surname_strokes  — 성씨 획수 반환
  2. calculate_name_suri  — 이름 획수로 81수리 4격 계산
  3. find_lucky_strokes   — 성씨 획수에 맞는 吉수 조합 역산
  4. lookup_ohaeng_combo  — 오행 3요소 조합의 운세 조회
  5. search_name_stats    — 이름 빈도 통계 조회
"""

import os
import json
import functools
import pandas as pd
from fastmcp import FastMCP

mcp = FastMCP("DBServer")

# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "..", "data", "raw", "reference")

def _load_json_with_comments(filepath: str):
    """JSON 파일 로드 (// 주석 라인 자동 제거)"""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    cleaned = "".join(line for line in lines if not line.strip().startswith("//"))
    return json.loads(cleaned)

def _load_suri_data() -> dict:
    """81suri.json → {숫자: {gyeok, fortune, gilhyung, description}}"""
    raw = _load_json_with_comments(os.path.join(DATA_DIR, "81suri.json"))
    suri_dict = {}
    for item in raw:
        num = item[0]
        suri_dict[num] = {
            "gyeok": item[1] if len(item) > 1 else "",
            "fortune": item[2] if len(item) > 2 else "",
            "gilhyung": item[3] if len(item) > 3 else "",
            "description": item[4] if len(item) > 4 else "",
        }
    return suri_dict

def _load_yinyang_data() -> dict:
    """yinyang.json → {"木木木": "설명..."}"""
    raw = _load_json_with_comments(os.path.join(DATA_DIR, "yinyang.json"))
    data = {}
    for item in raw:
        if len(item) >= 2:
            combo = item[0]
            data[combo] = item[1]
    return data

# 서버 시작 시 데이터 메모리에 로드 (82건 + 125건, 경량)
SURI_DATA = _load_suri_data()
YINYANG_DATA = _load_yinyang_data()

# ─────────────────────────────────────────────
# 예외 처리 방패 (Decorator)
# ─────────────────────────────────────────────
def safe_json_tool(func):
    """
    파이썬 내부 에러(TypeError, ValueError 등)가 발생해도 서버가 뻗지 않고,
    LLM이 이해할 수 있는 규격화된 [SYSTEM ERROR] JSON을 반환하게 덮어주는 데코레이터입니다.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": f"[SYSTEM ERROR] {e.__class__.__name__}: {str(e)}. ACTION REQUIRED: Check your input parameters."
            }, ensure_ascii=False)
    return wrapper

# 吉 판정 기준
GIL_TYPES = {"吉", "大吉", "中吉", "半吉"}

# 상생·상극 관계 테이블
SANGSAENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
SANGGEUK = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

SURNAME_STROKES = {
    "김": 8, "이": 7, "박": 6, "최": 11, "정": 19, "강": 9, "조": 14, "윤": 4, 
    "장": 11, "임": 8, "한": 17, "오": 7, "서": 10, "신": 5, "권": 22, "황": 12, 
    "안": 6, "송": 7, "전": 6, "홍": 10, "고": 10, "문": 4, "양": 11, "손": 10, 
    "배": 14, "백": 5, "허": 11, "유": 9, "남": 9, "심": 8, "노": 16, "하": 9,
    "곽": 15, "성": 7, "차": 7, "주": 6, "우": 9, "구": 8, "라": 20, "민": 13
}

def _get_suri_info(num: int) -> dict:
    """81수리 범위 보정 (81 초과 시 mod 81, 0이면 81)"""
    if num > 81:
        num = num % 81
        if num == 0:
            num = 81
    return SURI_DATA.get(num, {})


# ═══════════════════════════════════════════════════════
# Tool 0: 성씨 획수 조회
# ═══════════════════════════════════════════════════════
@mcp.tool()
@safe_json_tool
def get_surname_strokes(surname: str) -> str:
    """
    [LLM 필수 도구] 한국 성씨를 입력받아 원획법 기준 획수를 반환합니다.
    (예: '최', '김씨', '이' -> 11, 8, 7)
    주의: find_lucky_strokes 툴을 호출하기 전 반드시 이 툴로 성씨 획수를 구하세요.
    """
    clean_surname = surname.replace("씨", "").replace("가", "").strip()
    if clean_surname not in SURNAME_STROKES:
        return json.dumps({
            "status": "error",
            "message": f"[SYSTEM ERROR] Surname '{clean_surname}' not found in dictionary. ACTION REQUIRED: Provide strokes manually or default to 8."
        }, ensure_ascii=False)
        
    strokes = SURNAME_STROKES[clean_surname]
    return json.dumps({
        "status": "success",
        "data": {"surname": clean_surname, "surname_strokes": strokes},
        "message": f"{clean_surname}씨는 원획법 기준 {strokes}획입니다."
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# Tool 1: 81수리 4격 계산
# ═══════════════════════════════════════════════════════
@mcp.tool()
@safe_json_tool
def calculate_name_suri(
    surname_strokes: int,
    first_char_strokes: int,
    second_char_strokes: int,
) -> str:
    """
    성씨, 이름 첫째, 둘째 글자의 획수를 입력받아 81수리 4격을 계산합니다.
    """
    if surname_strokes <= 0 or first_char_strokes <= 0 or second_char_strokes <= 0:
        return json.dumps({
            "status": "error",
            "message": "[SYSTEM ERROR] Invalid strokes. ACTION REQUIRED: Ensure all stroke counts are positive integers."
        }, ensure_ascii=False)

    S = surname_strokes
    A = first_char_strokes
    B = second_char_strokes

    calculations = [
        ("원격(초년운)", A + B),
        ("형격(청년운)", S + A),
        ("이격(중년운)", S + B),
        ("정격(총운)", S + A + B),
    ]

    results = []
    all_gil = True
    hyung_count = 0
    data_output = {}

    for name, raw_value in calculations:
        info = _get_suri_info(raw_value)
        gyeok = info.get("gyeok", "정보 없음")
        fortune = info.get("fortune", "")
        gilhyung = info.get("gilhyung", "정보 없음")
        desc = info.get("description", "")

        lookup_num = raw_value % 81 if raw_value > 81 else raw_value
        if lookup_num == 0: lookup_num = 81
        display = f"{raw_value}수" if raw_value <= 81 else f"{raw_value}수(={lookup_num}수)"

        is_gil = gilhyung in GIL_TYPES
        if not is_gil:
            all_gil = False
            hyung_count += 1

        data_output[name] = {"value": raw_value, "gilhyung": gilhyung, "is_gil": is_gil}
        results.append(
            f"  {name}: {display} — {gyeok} / {fortune} / {gilhyung}\n    {desc}"
        )

    if all_gil:
        verdict = "[종합 판정: 吉] 4격 모두 길수입니다. 성명학적으로 좋은 이름입니다."
    else:
        verdict = f"[종합 판정: 凶] {hyung_count}개 격에 흉수가 포함되어 있습니다. 다른 획수 조합을 권장합니다."

    header = f"[81수리 4격 분석]\n입력: 성({S}획) + 이름 첫째({A}획) + 이름 둘째({B}획)\n"
    message = header + "\n" + "\n\n".join(results) + "\n\n" + verdict

    return json.dumps({
        "status": "success",
        "data": {"suri_4gyeok": data_output, "all_gil": all_gil},
        "message": message
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# Tool 2: 吉수 획수 조합 역산
# ═══════════════════════════════════════════════════════
@mcp.tool()
@safe_json_tool
def find_lucky_strokes(surname_strokes: int, max_strokes: int = 25) -> str:
    """
    성씨 획수(surname_strokes)를 입력받아 4격이 모두 吉인 이름 획수 조합을 역산합니다.
    주의: 이 툴을 쓰기 전에 반드시 get_surname_strokes 를 통해 성씨 획수를 얻으세요.
    """
    if surname_strokes <= 0:
        return json.dumps({
            "status": "error",
            "message": "[SYSTEM ERROR] Invalid surname_strokes. ACTION REQUIRED: Use get_surname_strokes to obtain valid integer first."
        }, ensure_ascii=False)

    S = surname_strokes
    lucky_combos = []

    for A in range(1, max_strokes + 1):
        for B in range(1, max_strokes + 1):
            won_info = _get_suri_info(A + B)
            hyung_info = _get_suri_info(S + A)
            yi_info = _get_suri_info(S + B)
            jung_info = _get_suri_info(S + A + B)

            if all(info.get("gilhyung", "") in GIL_TYPES for info in [won_info, hyung_info, yi_info, jung_info]):
                score = sum(3 if info["gilhyung"] == "大吉" else (2 if info["gilhyung"] == "吉" else 1) for info in [won_info, hyung_info, yi_info, jung_info])
                lucky_combos.append({
                    "a": A, "b": B,
                    "won": A + B, "won_g": won_info["gilhyung"],
                    "hyung": S + A, "hyung_g": hyung_info["gilhyung"],
                    "yi": S + B, "yi_g": yi_info["gilhyung"],
                    "jung": S + A + B, "jung_g": jung_info["gilhyung"],
                    "score": score
                })

    if not lucky_combos:
        return json.dumps({
            "status": "success",
            "data": {"lucky_combos": []},
            "message": f"[결과 없음] 성씨 {S}획에 대해 4격 모두 吉인 조합을 찾지 못했습니다."
        }, ensure_ascii=False)

    lucky_combos.sort(key=lambda x: -x["score"])
    
    lines = []
    for c in lucky_combos:
        lines.append(f"  ({c['a']}획, {c['b']}획) → 원격{c['won']}({c['won_g']}) 형격{c['hyung']}({c['hyung_g']}) 이격{c['yi']}({c['yi_g']}) 정격{c['jung']}({c['jung_g']})")

    header = f"[吉수 조합 역산] 성씨 {S}획 기준\n총 {len(lucky_combos)}개 조합 발견 (大吉 많은 순 정렬)\n"
    
    if len(lines) > 30:
        body = "\n".join(lines[:30])
        footer = f"\n\n... 외 {len(lines) - 30}개 조합 생략 (총 {len(lines)}개)"
    else:
        body = "\n".join(lines)
        footer = ""

    # JSON data에는 상위 10개만 리스트 형식으로 저장
    extracted_data = [{"first_char_strokes": c["a"], "second_char_strokes": c["b"]} for c in lucky_combos[:10]]

    return json.dumps({
        "status": "success",
        "data": {"surname_strokes": S, "recommended_combos": extracted_data},
        "message": header + "\n" + body + footer
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# Tool 3: 오행 조합 운세 조회
# ═══════════════════════════════════════════════════════
@mcp.tool()
@safe_json_tool
def lookup_ohaeng_combo(element1: str, element2: str, element3: str) -> str:
    """
    성씨·이름1·이름2의 오행(木/火/土/金/水) 조합에 대한 운세를 조회합니다.
    """
    valid = {"木", "火", "土", "金", "水"}

    for elem in [element1, element2, element3]:
        if elem not in valid:
            return json.dumps({
                "status": "error",
                "message": f"[SYSTEM ERROR] Invalid element '{elem}'. ACTION REQUIRED: Convert to one of '木', '火', '土', '金', '水' and retry."
            }, ensure_ascii=False)

    combo = element1 + element2 + element3
    desc = YINYANG_DATA.get(combo)

    if not desc:
        return json.dumps({
            "status": "error",
            "message": f"[SYSTEM ERROR] No data for combo '{combo}'. ACTION REQUIRED: Verify the element characters."
        }, ensure_ascii=False)

    relations = []
    for a, b, label in [(element1, element2, "성→이름1"), (element2, element3, "이름1→이름2")]:
        if SANGSAENG[a] == b: relations.append(f"  {label}: {a}생{b} (상생)")
        elif SANGGEUK[a] == b: relations.append(f"  {label}: {a}극{b} (상극)")
        elif a == b: relations.append(f"  {label}: {a}={b} (비화)")
        else:
            if SANGSAENG[b] == a: relations.append(f"  {label}: {b}생{a} (역상생)")
            elif SANGGEUK[b] == a: relations.append(f"  {label}: {b}극{a} (역상극)")
            else: relations.append(f"  {label}: {a}→{b}")

    pair1_sang = SANGSAENG[element1] == element2
    pair2_sang = SANGSAENG[element2] == element3
    if pair1_sang and pair2_sang: flow = "전체 상생 흐름 — 매우 좋은 조합입니다."
    elif pair1_sang or pair2_sang: flow = "부분 상생 — 보통 수준의 조합입니다."
    else: flow = "상생 흐름이 없음 — 주의가 필요한 조합입니다."

    header = f"[오행 조합 운세] {combo}\n\n"
    relation_text = "오행 관계:\n" + "\n".join(relations) + f"\n  흐름 판정: {flow}\n\n"
    body = f"운세 풀이:\n  {desc}"

    return json.dumps({
        "status": "success",
        "data": {"ohaeng_combo": combo, "flow": flow},
        "message": header + relation_text + body
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# Tool 4: 이름 빈도 통계 조회
# ═══════════════════════════════════════════════════════
_NAME_STATS_PATH = os.path.join(DATA_DIR, "2016_2026상위_출생신고_이름_현황.xls")
_name_stats_cache: pd.DataFrame | None = None

def _load_name_stats() -> pd.DataFrame:
    global _name_stats_cache
    if _name_stats_cache is None:
        _name_stats_cache = pd.read_excel(_NAME_STATS_PATH, header=0)
    return _name_stats_cache

@mcp.tool()
@safe_json_tool
def search_name_stats(name: str) -> str:
    """
    2016~2026년 출생신고 상위 이름 현황에서 특정 이름의 빈도/순위를 조회합니다.
    """
    try:
        df = _load_name_stats()
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"[SYSTEM ERROR] File load failed: {str(e)}"
        }, ensure_ascii=False)

    name_col = df.columns[1]
    matched = df[df[name_col].astype(str).str.strip() == name.strip()]

    if matched.empty:
        return json.dumps({
            "status": "success",
            "data": {"name": name, "found": False},
            "message": f"[결과 없음] '{name}'은(는) 상위 이름 목록에 없습니다."
        }, ensure_ascii=False)

    stats_list = []
    lines = [f"[이름 빈도 통계] '{name}' 검색 결과\n"]
    for _, row in matched.iterrows():
        stats_list.append({str(k): str(v) for k, v in row.items() if pd.notna(v)})
        lines.append("  " + " / ".join(f"{col}: {val}" for col, val in row.items() if pd.notna(val)))

    return json.dumps({
        "status": "success",
        "data": {"name": name, "found": True, "stats": stats_list},
        "message": "\n".join(lines)
    }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
