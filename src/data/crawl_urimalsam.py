# -*- coding: utf-8 -*-
"""
crawl_urimalsam.py — baby-name.kr 순우리말 이름 크롤링

대상: https://baby-name.kr/우리말이름/all/all/{page}/  (1~11페이지)
수집 항목: 이름, 뜻, 성별 경향, 최근 추세
출력: data/processed/urimalsam_names.json

실행 방법: python src/data/crawl_urimalsam.py
"""

import os
import sys
import json
import time
import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────

BASE_URL = "https://baby-name.kr/우리말이름/all/all/{page}/"
TOTAL_PAGES = 11
DELAY = 1.0  # 요청 간 딜레이 (초) — 서버 부하 방지

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "processed", "urimalsam_names.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


# ─────────────────────────────────────────────
# 크롤링
# ─────────────────────────────────────────────

def fetch_page(page: int) -> list[dict]:
    """단일 페이지에서 이름 데이터 수집."""
    url = BASE_URL.format(page=page)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  [오류] 페이지 {page} 요청 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="standard-table")
    if not table:
        print(f"  [경고] 페이지 {page} 테이블 없음")
        return []

    rows = table.find_all("tr")[1:]  # 헤더 제외
    records = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue

        name = cells[0].get_text(strip=True)
        meaning = cells[1].get_text(strip=True)
        gender = cells[2].get_text(strip=True)
        trend = cells[3].get_text(strip=True)

        if not name:
            continue

        records.append({
            "name": name,
            "meaning": meaning,
            "gender": gender,
            "trend": trend,
        })

    return records


def crawl_all() -> list[dict]:
    all_records = []

    for page in range(1, TOTAL_PAGES + 1):
        print(f"페이지 {page}/{TOTAL_PAGES} 수집 중...", end=" ")
        records = fetch_page(page)
        print(f"{len(records)}건")
        all_records.extend(records)

        if page < TOTAL_PAGES:
            time.sleep(DELAY)

    # 이름 기준 중복 제거
    seen = set()
    unique = []
    for r in all_records:
        if r["name"] not in seen:
            seen.add(r["name"])
            unique.append(r)

    return unique


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    print(f"순우리말 이름 크롤링 시작 (총 {TOTAL_PAGES}페이지)\n")
    records = crawl_all()

    print(f"\n총 {len(records)}개 이름 수집 완료")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"저장 완료: {OUTPUT_PATH}")

    print("\n샘플 5개:")
    for r in records[:5]:
        print(f"  {r['name']} — {r['meaning']} / {r['gender']} / {r['trend']}")


if __name__ == "__main__":
    main()
