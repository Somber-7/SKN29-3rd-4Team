"""
db_server.py — 수리/오행 연산 MCP 서버

81수리 4격 연산과 오행 조합 운세 조회를 담당합니다.
데이터: data/raw/reference/81suri.json, yinyang.json

[Tool 목록]
  1. calculate_name_suri  — 이름 획수로 81수리 4격 계산
  2. find_lucky_strokes   — 성씨 획수에 맞는 吉수 조합 역산
  3. lookup_ohaeng_combo  — 오행 3요소 조합의 운세 조회
"""

import os
import json
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
            combo = item[0].replace('\uf90a', '\u91d1')
            data[combo] = item[1]
    return data


# 서버 시작 시 데이터 메모리에 로드 (82건 + 125건, 경량)
SURI_DATA = _load_suri_data()
YINYANG_DATA = _load_yinyang_data()

# 吉 판정 기준
GIL_TYPES = {"吉", "大吉", "中吉", "半吉"}

# 상생·상극 관계 테이블
SANGSAENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
SANGGEUK = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}


def _get_suri_info(num: int) -> dict:
    """81수리 범위 보정 (81 초과 시 mod 81, 0이면 81)"""
    if num > 81:
        num = num % 81
        if num == 0:
            num = 81
    return SURI_DATA.get(num, {})


# ═══════════════════════════════════════════════════════
# Tool 1: 81수리 4격 계산
# ═══════════════════════════════════════════════════════
@mcp.tool()
def calculate_name_suri(
    surname_strokes: int,
    first_char_strokes: int,
    second_char_strokes: int,
) -> str:
    """
    성씨와 이름 각 글자의 획수를 입력받아 81수리 4격을 계산합니다.

    81수리 4격:
      - 원격(元格, 초년운) = 이름 첫째 글자 획수 + 이름 둘째 글자 획수
      - 형격(亨格, 청년운) = 성 획수 + 이름 첫째 글자 획수
      - 이격(利格, 중년운) = 성 획수 + 이름 둘째 글자 획수
      - 정격(貞格, 총운)   = 전체 합 (81 초과 시 mod 81)

    4격이 모두 吉이어야 좋은 이름으로 판정됩니다.

    주의: 획수는 반드시 원획법(강희자전 기준)으로 계산된 값을 사용하세요.
          필획법(일반 획수)과 다릅니다. (예: 삼수변 氵 = 필획법 3획 / 원획법 4획)

    Args:
        surname_strokes: 성씨 획수 (원획법 기준, 예: 김金=8)
        first_char_strokes: 이름 첫째 글자 획수 (예: 준俊=10)
        second_char_strokes: 이름 둘째 글자 획수 (예: 서瑞=14)

    Returns:
        4격 계산 결과 (각 격의 수리, 격명, 길흉, 설명 포함) + 종합 판정
    """
    S = surname_strokes
    A = first_char_strokes
    B = second_char_strokes

    # 4격 계산
    calculations = [
        ("원격(초년운)", A + B),
        ("형격(청년운)", S + A),
        ("이격(중년운)", S + B),
        ("정격(총운)", S + A + B),
    ]

    results = []
    all_gil = True
    hyung_count = 0

    for name, raw_value in calculations:
        info = _get_suri_info(raw_value)
        gyeok = info.get("gyeok", "정보 없음")
        fortune = info.get("fortune", "")
        gilhyung = info.get("gilhyung", "정보 없음")
        desc = info.get("description", "")

        # 81 초과 보정 표시
        lookup_num = raw_value % 81 if raw_value > 81 else raw_value
        if lookup_num == 0:
            lookup_num = 81
        display = f"{raw_value}수" if raw_value <= 81 else f"{raw_value}수(={lookup_num}수)"

        is_gil = gilhyung in GIL_TYPES
        if not is_gil:
            all_gil = False
            hyung_count += 1

        results.append(
            f"  {name}: {display} — {gyeok} / {fortune} / {gilhyung}\n"
            f"    {desc}"
        )

    # 종합 판정
    if all_gil:
        verdict = "[종합 판정: 吉] 4격 모두 길수입니다. 성명학적으로 좋은 이름입니다."
    else:
        verdict = f"[종합 판정: 凶] {hyung_count}개 격에 흉수가 포함되어 있습니다. 다른 획수 조합을 권장합니다."

    header = (
        f"[81수리 4격 분석]\n"
        f"입력: 성({S}획) + 이름 첫째({A}획) + 이름 둘째({B}획)\n"
    )

    return header + "\n" + "\n\n".join(results) + "\n\n" + verdict


# ═══════════════════════════════════════════════════════
# Tool 2: 吉수 획수 조합 역산
# ═══════════════════════════════════════════════════════
@mcp.tool()
def find_lucky_strokes(surname_strokes: int, max_strokes: int = 25) -> str:
    """
    성씨 획수를 입력받아 81수리 4격이 모두 吉인 이름 획수 조합을 역산합니다.

    호출 조건:
      - 사용자가 "김씨 성에 어울리는 획수 조합 찾아줘"라고 요청한 경우
      - 이름 추천 전, 吉수 조합을 먼저 확보하고 해당 획수의 한자를 검색할 때

    호출 흐름:
      1) find_lucky_strokes(surname_strokes=8) → 吉수 획수 조합 목록 확보
      2) 획수 조합으로 peoplehanja.json / ChromaDB에서 해당 획수 한자 검색
      3) 오행·뜻 등 추가 조건 필터링 후 최종 추천

    Args:
        surname_strokes: 성씨 획수 (원획법 기준, 예: 김=8)
        max_strokes: 이름 한 글자당 최대 획수 범위 (기본값: 25)

    Returns:
        4격 모두 吉인 (첫째글자 획수, 둘째글자 획수) 조합 목록
    """
    S = surname_strokes
    lucky_combos = []

    for A in range(1, max_strokes + 1):
        for B in range(1, max_strokes + 1):
            won_info = _get_suri_info(A + B)
            hyung_info = _get_suri_info(S + A)
            yi_info = _get_suri_info(S + B)
            jung_info = _get_suri_info(S + A + B)

            if all(
                info.get("gilhyung", "") in GIL_TYPES
                for info in [won_info, hyung_info, yi_info, jung_info]
            ):
                won_g = won_info["gilhyung"]
                hyung_g = hyung_info["gilhyung"]
                yi_g = yi_info["gilhyung"]
                jung_g = jung_info["gilhyung"]

                lucky_combos.append({
                    "a": A, "b": B,
                    "won": A + B, "won_g": won_g,
                    "hyung": S + A, "hyung_g": hyung_g,
                    "yi": S + B, "yi_g": yi_g,
                    "jung": S + A + B, "jung_g": jung_g,
                })

    if not lucky_combos:
        return (
            f"[결과 없음] 성씨 {S}획에 대해 1~{max_strokes}획 범위에서 "
            f"4격 모두 吉인 조합을 찾지 못했습니다."
        )

    # 大吉이 많은 조합 우선 정렬
    def sort_key(c):
        score = 0
        for g in [c["won_g"], c["hyung_g"], c["yi_g"], c["jung_g"]]:
            if g == "大吉":
                score += 3
            elif g == "吉":
                score += 2
            elif g == "中吉":
                score += 1
        return -score

    lucky_combos.sort(key=sort_key)

    lines = []
    for c in lucky_combos:
        lines.append(
            f"  ({c['a']}획, {c['b']}획) → "
            f"원격{c['won']}({c['won_g']}) "
            f"형격{c['hyung']}({c['hyung_g']}) "
            f"이격{c['yi']}({c['yi_g']}) "
            f"정격{c['jung']}({c['jung_g']})"
        )

    header = (
        f"[吉수 조합 역산] 성씨 {S}획 기준\n"
        f"총 {len(lucky_combos)}개 조합 발견 (大吉 많은 순 정렬)\n"
    )

    # 30개 초과 시 상위만 표시
    if len(lines) > 30:
        body = "\n".join(lines[:30])
        footer = f"\n\n... 외 {len(lines) - 30}개 조합 생략 (총 {len(lines)}개)"
    else:
        body = "\n".join(lines)
        footer = ""

    return header + "\n" + body + footer


# ═══════════════════════════════════════════════════════
# Tool 3: 오행 조합 운세 조회
# ═══════════════════════════════════════════════════════
@mcp.tool()
def lookup_ohaeng_combo(element1: str, element2: str, element3: str) -> str:
    """
    성씨·이름1·이름2의 오행 조합에 대한 운세를 조회합니다.

    오행 5가지: 木(목), 火(화), 土(토), 金(금), 水(수)

    호출 조건:
      - LLM이 이름을 추천한 후 오행 조합의 운세를 확인할 때
      - 사용자가 "이 이름의 오행 궁합이 어때?"라고 물었을 때
      - 상생(좋은 조합)인지 상극(나쁜 조합)인지 판단이 필요할 때

    오행 상생 순환: 木→火→土→金→水→木 (서로 살리는 관계)
    오행 상극 순환: 木→土, 土→水, 水→火, 火→金, 金→木 (서로 꺾는 관계)

    Args:
        element1: 성씨의 오행 (木/火/土/金/水)
        element2: 이름 첫째 글자의 오행 (木/火/土/金/水)
        element3: 이름 둘째 글자의 오행 (木/火/土/金/水)

    Returns:
        오행 관계 분석 + 해당 조합의 운세 설명
    """
    valid = {"木", "火", "土", "金", "水"}

    # 입력값 정규화 (호환용 한자 金을 통합 한자 金으로 변경)
    element1 = element1.replace('\uf90a', '\u91d1')
    element2 = element2.replace('\uf90a', '\u91d1')
    element3 = element3.replace('\uf90a', '\u91d1')

    for elem, label in [(element1, "성씨"), (element2, "이름1"), (element3, "이름2")]:
        if elem not in valid:
            return (
                f"[오류] {label}의 오행 '{elem}'이(가) 유효하지 않습니다. "
                f"木/火/土/金/水 중 하나를 입력하세요."
            )

    combo = element1 + element2 + element3
    desc = YINYANG_DATA.get(combo)

    if not desc:
        return f"[결과 없음] '{combo}' 조합에 대한 운세 정보를 찾을 수 없습니다."

    # 상생/상극 관계 분석
    relations = []
    for a, b, label in [(element1, element2, "성→이름1"), (element2, element3, "이름1→이름2")]:
        if SANGSAENG[a] == b:
            relations.append(f"  {label}: {a}생{b} (상생 — 살려주는 관계)")
        elif SANGGEUK[a] == b:
            relations.append(f"  {label}: {a}극{b} (상극 — 꺾는 관계)")
        elif a == b:
            relations.append(f"  {label}: {a}={b} (비화 — 같은 오행)")
        else:
            # 역상생/역상극 체크
            if SANGSAENG[b] == a:
                relations.append(f"  {label}: {b}생{a} (역상생)")
            elif SANGGEUK[b] == a:
                relations.append(f"  {label}: {b}극{a} (역상극)")
            else:
                relations.append(f"  {label}: {a}→{b}")

    # 전체 흐름 판정
    pair1_sang = SANGSAENG[element1] == element2
    pair2_sang = SANGSAENG[element2] == element3
    if pair1_sang and pair2_sang:
        flow = "전체 상생 흐름 — 매우 좋은 조합입니다."
    elif pair1_sang or pair2_sang:
        flow = "부분 상생 — 보통 수준의 조합입니다."
    else:
        flow = "상생 흐름이 없음 — 주의가 필요한 조합입니다."

    header = f"[오행 조합 운세] {combo}\n\n"
    relation_text = "오행 관계:\n" + "\n".join(relations) + f"\n  흐름 판정: {flow}\n\n"
    body = f"운세 풀이:\n  {desc}"

    return header + relation_text + body


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
def search_name_stats(name: str) -> str:
    """
    2016~2026년 출생신고 이름 현황에서 특정 이름의 빈도 통계를 조회합니다.

    호출 조건:
      - 추천 이름이 실제로 많이 쓰이는 이름인지 확인할 때
      - 사용자가 "이 이름이 흔한 편인가요?"라고 물었을 때

    Args:
        name: 조회할 이름 (예: "서연", "민준")

    Returns:
        해당 이름의 순위, 비율, 건수 정보
    """
    try:
        df = _load_name_stats()
    except FileNotFoundError:
        return f"[오류] 이름 통계 파일을 찾을 수 없습니다: {_NAME_STATS_PATH}"
    except Exception as e:
        return f"[오류] 파일 로드 실패: {str(e)}"

    # 이름 컬럼 자동 탐색 (컬럼명이 다를 수 있음)
    name_col = None
    for col in df.columns:
        if "이름" in str(col) or "성명" in str(col):
            name_col = col
            break
    if name_col is None:
        name_col = df.columns[1]  # 일반적으로 2번째 컬럼

    matched = df[df[name_col].astype(str).str.contains(name, na=False)]

    if matched.empty:
        return (
            f"[결과 없음] '{name}'은(는) 2016~2026년 출생신고 상위 이름 목록에 없습니다.\n"
            f"흔하지 않은 이름이거나 독특한 이름일 가능성이 높습니다."
        )

    lines = [f"[이름 빈도 통계] '{name}' 검색 결과\n"]
    for _, row in matched.iterrows():
        lines.append("  " + " / ".join(f"{col}: {val}" for col, val in row.items() if pd.notna(val)))

    return "\n".join(lines)


# ─────────────────────────────────────────────
# 서버 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
