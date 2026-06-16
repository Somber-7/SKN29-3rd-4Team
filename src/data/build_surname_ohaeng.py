"""
성씨 오행 데이터 생성 스크립트.
data/raw/reference/성씨 유래 정보.csv + data/raw/reference/peoplehanja.json 에서
259개 한국 성씨 한자의 자원오행을 추출해 data/processed/surname_ohaeng.json 에 저장한다.

peoplehanja.json에 없는 3개 (曾, 范, 蔣)는 부수 기반 자원오행으로 수동 보완.
"""
import csv
import json
import unicodedata
from pathlib import Path

BASE = Path(__file__).parent.parent
CSV_PATH  = BASE / "data/raw/reference/성씨 유래 정보.csv"
HANJA_PATH = BASE / "data/raw/reference/peoplehanja.json"
OUT_PATH  = BASE / "data/processed/surname_ohaeng.json"

# peoplehanja.json에 없는 3개 수동 보완 (부수 기반 자원오행)
MANUAL = {
    "曾": {"hangul": "증", "resource_ohaeng": "토"},  # 八부수 → 土
    "范": {"hangul": "범", "resource_ohaeng": "목"},  # 艸부수 → 木
    "蔣": {"hangul": "장", "resource_ohaeng": "목"},  # 艸부수 → 木
}

_HANJA_TO_KR = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}


def load_hanja_lookup() -> dict[str, dict]:
    """peoplehanja.json에서 hanja → {hangul, resource_ohaeng} 매핑 생성.
    형식: [hanja, 획수오행, 자원오행, strokes, meanings, radical, hangul_sound]
    col[2] = 자원오행, col[6] = hangul_sound
    """
    with open(HANJA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    lookup: dict[str, dict] = {}
    for row in data:
        if not isinstance(row, list) or len(row) < 7:
            continue
        hanja = row[0]
        resource_ohaeng = row[2]
        hangul = row[6]
        if hanja and resource_ohaeng and hangul:
            hanja = unicodedata.normalize("NFKC", hanja)
            norm = unicodedata.normalize("NFKC", resource_ohaeng)
            ohaeng_kr = _HANJA_TO_KR.get(norm, resource_ohaeng)
            lookup[hanja] = {"hangul": hangul, "resource_ohaeng": ohaeng_kr}
    return lookup


def load_surname_hanja_map() -> dict[str, str]:
    """CSV에서 성씨 한자 → 한글명 매핑 생성.
    BOM 포함 파일이므로 utf-8-sig로 열어 첫 컬럼 키 정상화.
    같은 한자가 여러 본관으로 반복 등장할 때 한글명이 있는 첫 행을 사용.
    """
    hanja_to_hangul: dict[str, str] = {}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            hangul = row.get("성씨 한글명", "").strip()
            hanja  = row.get("성씨 한자명", "").strip()
            if not hanja or len(hanja) != 1:
                continue
            hanja = unicodedata.normalize("NFKC", hanja)
            # 이미 등록됐거나 한글명이 없으면 스킵
            if hanja not in hanja_to_hangul and hangul:
                hanja_to_hangul[hanja] = hangul
    return hanja_to_hangul


def build() -> None:
    hanja_lookup    = load_hanja_lookup()
    surname_hangul  = load_surname_hanja_map()

    result: dict[str, dict] = {}

    for hanja, hangul in surname_hangul.items():
        if hanja in hanja_lookup:
            entry = hanja_lookup[hanja].copy()
            entry["hangul"] = hangul          # CSV의 발음이 더 신뢰성 높음
            result[hanja] = entry
        elif hanja in MANUAL:
            result[hanja] = MANUAL[hanja]
        else:
            print(f"[미발견] {hanja} ({hangul}) — peoplehanja에 없고 수동 데이터도 없음")

    # MANUAL 항목 중 CSV에 없는 것도 추가
    for hanja, entry in MANUAL.items():
        if hanja not in result:
            result[hanja] = entry

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"저장 완료: {OUT_PATH} ({len(result)}개)")

    # 검증 출력
    missing_ohaeng = [h for h, v in result.items() if not v.get("resource_ohaeng")]
    if missing_ohaeng:
        print(f"[경고] resource_ohaeng 없는 성씨: {missing_ohaeng}")


if __name__ == "__main__":
    build()
