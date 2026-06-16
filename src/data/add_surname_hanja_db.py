"""
성씨 한자 DB 추가 스크립트.

data/processed/surname_ohaeng.json의 성씨 한자 중 hanja_col에 없는 것을
ChromaDB와 Neo4j에 추가한다.

ChromaDB:
  - 이미 hanja_col에 있는 성씨 한자는 건드리지 않음
  - 없는 것만 upsert (is_person_name_hanja=False, is_surname_hanja=True)

Neo4j:
  - MERGE로 Hanja 노드 추가 (이미 있으면 속성만 갱신)
  - BELONGS_TO 오행 관계 연결
  - PERMITTED_BY 없음 (인명용 한자가 아님)

실행:
  python scripts/add_surname_hanja_db.py            # dry-run (ChromaDB만)
  python scripts/add_surname_hanja_db.py --execute  # ChromaDB + Neo4j 실제 적재
"""
import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

try:
    from neo4j import GraphDatabase
    _NEO4J_OK = True
except Exception:
    _NEO4J_OK = False

# ─── 설정 ───────────────────────────────────────────────
MODEL_NAME       = "jhgan/ko-sroberta-multitask"
COLLECTION_NAME  = "hanja_col"
DATASET          = "hanja_profiles"
NEO4J_LAW_ID     = "person_name_hanja_rule"

BASE     = Path(__file__).parent.parent
CHROMA   = BASE / "data" / "chroma"
SURNAME  = BASE / "data" / "processed" / "surname_ohaeng.json"
PEOPLE   = BASE / "data" / "raw" / "reference" / "peoplehanja.json"
HANJA_DOCS = BASE / "data" / "processed" / "hanja_documents.json"

# 발음오행 — 한글 초성 기준
_INITIAL_TO_OHAENG = {
    "ㄱ": "목", "ㅋ": "목",
    "ㄴ": "화", "ㄹ": "화",
    "ㅇ": "토", "ㅎ": "토",
    "ㅅ": "금", "ㅈ": "금", "ㅊ": "금",
    "ㅁ": "수", "ㅂ": "수", "ㅍ": "수",
}
_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def _sound_ohaeng(hangul: str) -> str:
    """한글 첫 글자의 초성으로 발음오행을 반환한다."""
    if not hangul:
        return ""
    c = hangul[0]
    if "가" <= c <= "힣":
        idx = (ord(c) - 0xAC00) // 588
        initial = _INITIALS[idx]
        return _INITIAL_TO_OHAENG.get(initial, "")
    return ""


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def load_surname_ohaeng() -> dict[str, dict]:
    with open(SURNAME, encoding="utf-8") as f:
        return json.load(f)


def load_people_lookup() -> dict[str, dict]:
    """peoplehanja.json → {NFKC(hanja): {strokes, sound_meaning, radical}}"""
    with open(PEOPLE, encoding="utf-8") as f:
        data = json.load(f)
    lookup: dict[str, dict] = {}
    for row in data:
        if not isinstance(row, list) or len(row) < 7:
            continue
        hanja = _nfkc(row[0])
        meanings = row[4] if row[4] else ""
        # 첫 번째 의미 추출 (콤마 구분, '姓' 제외)
        parts = [p.strip() for p in str(meanings).split(",") if p.strip() and p.strip() != "姓"]
        sound_meaning = parts[0] if parts else str(meanings).split(",")[0].strip()
        lookup[hanja] = {
            "strokes": int(row[3]) if isinstance(row[3], (int, float)) else 0,
            "sound_meaning": sound_meaning,
            "radical": row[5] if len(row) > 5 else "",
        }
    return lookup


def load_existing_hanja_set() -> set[str]:
    """hanja_col에 이미 있는 한자 집합을 반환한다."""
    with open(HANJA_DOCS, encoding="utf-8") as f:
        docs = json.load(f)
    return {rec["metadata"]["hanja"] for rec in docs}


def build_records(
    surnames: dict[str, dict],
    people: dict[str, dict],
    existing: set[str],
) -> list[dict]:
    """hanja_col에 없는 성씨 한자의 ChromaDB 레코드를 생성한다."""
    records = []
    idx = 1
    for hanja, info in surnames.items():
        if hanja in existing:
            continue
        hangul        = info["hangul"]
        resource_oe   = info["resource_ohaeng"]
        sound_oe      = _sound_ohaeng(hangul)
        pdata         = people.get(_nfkc(hanja), {})
        strokes       = pdata.get("strokes", 0)
        sound_meaning = pdata.get("sound_meaning", "")
        unicode_val   = f"U+{ord(hanja):04X}"
        profile_id    = f"SUR-{idx:05d}"
        document = (
            f"한자 {hanja}({hangul})은 뜻이 '{sound_meaning}'인 성씨 한자이다. "
            f"유니코드는 {unicode_val}, 획수는 {strokes}획, "
            f"발음오행은 {sound_oe}, 자원오행은 {resource_oe}이다."
        )
        records.append({
            "id": f"hanja_{profile_id}",
            "document": document,
            "metadata": {
                "type": "hanja",
                "collection": DATASET,
                "source": "surname_ohaeng.json",
                "profile_id": profile_id,
                "hangul": hangul,
                "hanja": hanja,
                "unicode": unicode_val,
                "sound_meaning": sound_meaning,
                "strokes": strokes if strokes > 0 else 1,
                "sound_ohaeng": sound_oe if sound_oe else resource_oe,
                "resource_ohaeng": resource_oe,
                "is_person_name_hanja": False,
                "is_surname_hanja": True,
            },
        })
        idx += 1
    return records


# ─── ChromaDB ───────────────────────────────────────────

def upsert_chroma(records: list[dict]) -> None:
    client = chromadb.PersistentClient(path=str(CHROMA))
    ef = SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)
    col = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
    col.upsert(
        ids=[r["id"] for r in records],
        documents=[r["document"] for r in records],
        metadatas=[r["metadata"] for r in records],
    )
    print(f"ChromaDB upsert 완료: {len(records)}개 → {COLLECTION_NAME} (총 {col.count()}개)")


# ─── Neo4j ──────────────────────────────────────────────

_UPSERT_SURNAME_HANJA = """
UNWIND $rows AS row
MERGE (h:Hanja {dataset: $dataset, profile_id: row.profile_id})
SET h.hanja           = row.hanja,
    h.hangul          = row.hangul,
    h.unicode         = row.unicode,
    h.sound_meaning   = row.sound_meaning,
    h.strokes         = row.strokes,
    h.sound_ohaeng    = row.sound_ohaeng,
    h.resource_ohaeng = row.resource_ohaeng,
    h.is_person_name_hanja = false,
    h.is_surname_hanja     = true,
    h.source          = row.source,
    h.updated_by      = 'scripts/add_surname_hanja_db.py'
WITH h, row
MERGE (sound_cat:Category:Ohaeng {dataset: $dataset, name: row.sound_ohaeng})
MERGE (res_cat:Category:Ohaeng   {dataset: $dataset, name: row.resource_ohaeng})
MERGE (h)-[:BELONGS_TO {kind: 'sound_ohaeng'}]->(sound_cat)
MERGE (h)-[:BELONGS_TO {kind: 'resource_ohaeng'}]->(res_cat)
"""


def upsert_neo4j(records: list[dict], uri: str, user: str, password: str, database: str) -> None:
    if not _NEO4J_OK:
        print("[오류] neo4j 패키지가 없습니다.")
        return
    rows = [
        {
            "profile_id":    r["metadata"]["profile_id"],
            "hanja":         r["metadata"]["hanja"],
            "hangul":        r["metadata"]["hangul"],
            "unicode":       r["metadata"]["unicode"],
            "sound_meaning": r["metadata"]["sound_meaning"],
            "strokes":       r["metadata"]["strokes"],
            "sound_ohaeng":  r["metadata"]["sound_ohaeng"],
            "resource_ohaeng": r["metadata"]["resource_ohaeng"],
            "source":        r["metadata"]["source"],
        }
        for r in records
    ]
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run(_UPSERT_SURNAME_HANJA, rows=rows, dataset=DATASET)
        print(f"Neo4j upsert 완료: {len(records)}개 Hanja 노드 추가/갱신")
    finally:
        driver.close()


# ─── CLI ────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="성씨 한자를 ChromaDB/Neo4j에 추가합니다.")
    parser.add_argument("--execute", action="store_true", help="실제 DB에 쓰기 (기본: dry-run)")
    parser.add_argument("--neo4j-only", action="store_true", help="Neo4j만 실행")
    parser.add_argument("--chroma-only", action="store_true", help="ChromaDB만 실행")
    parser.add_argument("--database", default="neo4j", help="Neo4j 데이터베이스 이름")
    args = parser.parse_args()

    print("데이터 로드 중...")
    surnames = load_surname_ohaeng()
    people   = load_people_lookup()
    existing = load_existing_hanja_set()

    records = build_records(surnames, people, existing)
    print(f"surname_ohaeng.json: {len(surnames)}개")
    print(f"hanja_col 기존: {len(existing)}개")
    print(f"추가 대상: {len(records)}개")
    for r in records:
        m = r["metadata"]
        print(f"  {m['hanja']}({m['hangul']}) | 획수={m['strokes']} | "
              f"발음오행={m['sound_ohaeng']} | 자원오행={m['resource_ohaeng']}")

    if not records:
        print("추가할 성씨 한자가 없습니다.")
        return

    if not args.execute:
        print("\ndry-run 완료. --execute 옵션을 붙여 실제 적재하세요.")
        return

    run_chroma = not args.neo4j_only
    run_neo4j  = not args.chroma_only

    if run_chroma:
        print("\nChromaDB upsert 중...")
        upsert_chroma(records)

    if run_neo4j:
        uri      = os.getenv("NEO4J_URI")
        user     = os.getenv("NEO4J_USER")
        password = os.getenv("NEO4J_PASSWORD")
        if not all([uri, user, password]):
            print("[경고] NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD 환경 변수가 없어 Neo4j 스킵.")
        else:
            print("\nNeo4j upsert 중...")
            upsert_neo4j(records, str(uri), str(user), str(password), args.database)

    print("\n완료.")


if __name__ == "__main__":
    main()
