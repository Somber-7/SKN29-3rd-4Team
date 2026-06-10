"""
process_suri.py — 81수리 / 오행 조합 데이터 전처리

원본 JSON 파일(81suri.json, yinyang.json)을 ChromaDB 인덱싱에 적합한
구조화된 문서(Document + Metadata) 형태로 변환하여 저장합니다.

입력: data/raw/reference/81suri.json, yinyang.json
출력: data/processed/suri_documents.json, ohaeng_documents.json

이후 ChromaDB 인덱싱 시 이 파일을 로드하여 바로 사용할 수 있습니다.
"""

import os
import json
import re

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "..", "..", "data", "raw", "reference")
PROCESSED_DIR = os.path.join(BASE_DIR, "..", "..", "data", "processed")


def _load_json_with_comments(filepath: str):
    """JSON 파일 로드 (// 주석 라인 자동 제거)"""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    cleaned = "".join(line for line in lines if not line.strip().startswith("//"))
    return json.loads(cleaned)


def split_ko_hanja(text):
    """괄호와 한자를 분리 (예: '발전격(發展格)' -> '발전격', '發展格')"""
    match = re.match(r"(.+?)\((.+?)\)", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text.strip(), ""


# ═══════════════════════════════════════════════════════
# 1. 81수리 데이터 전처리
# ═══════════════════════════════════════════════════════
def process_81suri():
    """
    81suri.json → ChromaDB용 구조화 문서 변환

    각 수리(0~81)를 하나의 Document로 변환:
      - id: "suri_1", "suri_2", ...
      - document: 임베딩할 텍스트 (격명 + 길흉 + 설명)
      - metadata: 필터링용 구조화 데이터
    """
    raw = _load_json_with_comments(os.path.join(RAW_DIR, "81suri.json"))

    documents = []
    for item in raw:
        num = item[0]
        gyeok = item[1] if len(item) > 1 else ""
        fortune = item[2] if len(item) > 2 else ""
        gilhyung = item[3] if len(item) > 3 else ""
        description = item[4] if len(item) > 4 else ""

        # 0수는 빈 데이터이므로 건너뜀
        if num == 0 or not description:
            continue

        gyeok_ko, gyeok_hanja = split_ko_hanja(gyeok)
        fortune_ko, fortune_hanja = split_ko_hanja(fortune)

        # ChromaDB에 인덱싱할 텍스트
        # → 임베딩 검색 시 "16획 운세", "吉수 설명" 등으로 검색 가능
        doc_text = (
            f"{num}수 {gyeok_ko}({gyeok_hanja}) {fortune_ko}({fortune_hanja}) [{gilhyung}] — {description}"
        )

        documents.append({
            "id": f"suri_{num}",
            "document": doc_text,
            "metadata": {
                "type": "81suri",
                "suri_num": num,
                "gyeok_ko": gyeok_ko,
                "gyeok_hanja": gyeok_hanja,
                "fortune_ko": fortune_ko,
                "fortune_hanja": fortune_hanja,
                "gilhyung": gilhyung,
            },
        })

    return documents


# ═══════════════════════════════════════════════════════
# 2. 오행 조합 데이터 전처리
# ═══════════════════════════════════════════════════════
def process_yinyang():
    """
    yinyang.json → ChromaDB용 구조화 문서 변환

    각 오행 조합(125개)을 하나의 Document로 변환:
      - id: "ohaeng_木木木", ...
      - document: 임베딩할 텍스트 (조합 + 운세 설명)
      - metadata: 필터링용 구조화 데이터
    """
    raw = _load_json_with_comments(os.path.join(RAW_DIR, "yinyang.json"))

    # 상생·상극 판정용
    sangsaeng = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    sanggeuk = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

    documents = []
    for idx, item in enumerate(raw, 1):
        combo = item[0]
        # Normalize 金 (0xf90a) to 金 (0x91d1)
        combo = combo.replace('\uf90a', '\u91d1')
        description = item[1] if len(item) > 1 else ""

        if not description:
            continue

        e1, e2, e3 = combo[0], combo[1], combo[2]

        # 상생/상극 흐름 분석
        pair1 = "상생" if sangsaeng[e1] == e2 else ("상극" if sanggeuk[e1] == e2 else "비화")
        pair2 = "상생" if sangsaeng[e2] == e3 else ("상극" if sanggeuk[e2] == e3 else "비화")

        if pair1 == "상생" and pair2 == "상생":
            flow = "전체상생"
        elif "상극" in [pair1, pair2]:
            flow = "상극포함"
        elif pair1 == "비화" and pair2 == "비화":
            flow = "전체비화"
        else:
            flow = "부분상생"

        # ChromaDB에 인덱싱할 텍스트
        doc_text = (
            f"오행 조합 {combo} ({e1}→{e2}→{e3}) — {description}"
        )

        documents.append({
            "id": f"ohaeng_{idx}",
            "document": doc_text,
            "metadata": {
                "type": "yinyang",
                "combo": combo,
                "element1": e1,
                "element2": e2,
                "element3": e3,
                "pair1_relation": pair1,
                "pair2_relation": pair2,
                "flow": flow,
            },
        })

    return documents


# ═══════════════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════════════
def main():
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    # 81수리 처리
    suri_docs = process_81suri()
    suri_path = os.path.join(PROCESSED_DIR, "suri_documents.json")
    with open(suri_path, "w", encoding="utf-8") as f:
        json.dump(suri_docs, f, ensure_ascii=False, indent=2)
    print(f"[완료] 81수리 문서 {len(suri_docs)}건 → {suri_path}")

    # 오행 조합 처리
    ohaeng_docs = process_yinyang()
    ohaeng_path = os.path.join(PROCESSED_DIR, "ohaeng_documents.json")
    with open(ohaeng_path, "w", encoding="utf-8") as f:
        json.dump(ohaeng_docs, f, ensure_ascii=False, indent=2)
    print(f"[완료] 오행 조합 문서 {len(ohaeng_docs)}건 → {ohaeng_path}")

    # 요약 출력
    print("\n[전처리 요약]")
    print(f"  81수리: {len(suri_docs)}건 (吉: {sum(1 for d in suri_docs if d['metadata']['gilhyung'] in ('吉','大吉','中吉','半吉'))}건 / 凶: {sum(1 for d in suri_docs if d['metadata']['gilhyung'] in ('凶','大凶'))}건)")
    
    flow_counts = {}
    for d in ohaeng_docs:
        flow = d["metadata"]["flow"]
        flow_counts[flow] = flow_counts.get(flow, 0) + 1
    print(f"  오행 조합: {len(ohaeng_docs)}건 (전체상생: {flow_counts.get('전체상생', 0)}건 / 부분상생: {flow_counts.get('부분상생', 0)}건 / 전체비화: {flow_counts.get('전체비화', 0)}건 / 상극포함: {flow_counts.get('상극포함', 0)}건 / 혼합: {flow_counts.get('혼합', 0)}건)")


if __name__ == "__main__":
    main()
