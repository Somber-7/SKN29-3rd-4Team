"""Index Hanja document/metadata records into Neo4j.

Project guide: docs/project_idea_naming.md
Progress checklist: docs/진행_체크리스트.md

This script follows the guide's Neo4j graph direction:
- Hanja, Law, Category nodes
- BELONGS_TO, PERMITTED_BY, GENERATES, CONTROLS relationships
- graph_db route for Hanja-Ohaeng-Law traversal

The source of truth is the processed ChromaDB input file
data/processed/hanja_documents.json. Raw files are not reprocessed here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - handled at runtime
    load_dotenv = None

try:
    from neo4j import GraphDatabase
except Exception as exc:  # pragma: no cover - handled at runtime
    GraphDatabase = None
    NEO4J_IMPORT_ERROR = exc
else:
    NEO4J_IMPORT_ERROR = None


# 1. 프로젝트 가이드 및 기본 설정
# docs/project_idea_naming.md의 Graph DB 설계에 맞춰 한자-오행-법령 관계를 만든다.
PROJECT_GUIDE_PATH = "docs/project_idea_naming.md"
PROGRESS_CHECKLIST_PATH = "docs/진행_체크리스트.md"
DEFAULT_SOURCE = "data/processed/hanja_documents.json"
DEFAULT_DATASET = "hanja_profiles"
DEFAULT_DATABASE = "neo4j"
DEFAULT_BATCH_SIZE = 500

OHAENG_VALUES = ["목", "화", "토", "금", "수"]
OHAENG_SET = set(OHAENG_VALUES)
OHAENG_GENERATES = [
    ("목", "화"),
    ("화", "토"),
    ("토", "금"),
    ("금", "수"),
    ("수", "목"),
]
OHAENG_CONTROLS = [
    ("목", "토"),
    ("토", "수"),
    ("수", "화"),
    ("화", "금"),
    ("금", "목"),
]

LAW_NODE = {
    "law_id": "person_name_hanja_rule",
    "name": "인명용 한자 규정",
    "basis": "metadata.is_person_name_hanja",
    "description": "project_idea_naming.md의 PERMITTED_BY 관계 생성을 위한 인명용 한자 허용 근거 노드",
}

REQUIRED_METADATA_FIELDS = [
    "profile_id",
    "hangul",
    "hanja",
    "unicode",
    "sound_meaning",
    "strokes",
    "sound_ohaeng",
    "resource_ohaeng",
    "is_person_name_hanja",
]


# 2. 콘솔, 경로, 환경 변수 처리
# Windows 콘솔 인코딩과 repo 기준 상대 경로를 안정적으로 처리한다.
def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_project_env(root: Path) -> None:
    if load_dotenv is not None:
        load_dotenv(root / ".env")


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got: {value}") from None


# 3. 입력 document/metadata 검증
# ChromaDB와 같은 id/document/metadata 구조를 읽고 Neo4j에 넣기 전 품질 이슈를 잡는다.
def validate_records(records: Any, default_dataset: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not isinstance(records, list):
        return rows, [{"issue": "source_json_must_be_list"}], warnings

    seen_ids: set[str] = set()
    seen_profile_ids: set[str] = set()
    unicode_to_profiles: dict[str, list[str]] = {}
    sound_to_ohaeng: dict[str, set[str]] = {}

    for index, item in enumerate(records, 1):
        if not isinstance(item, dict):
            errors.append({"index": index, "issue": "item_not_object"})
            continue

        doc_id = item.get("id")
        document = item.get("document")
        metadata = item.get("metadata")

        if not isinstance(doc_id, str) or not doc_id.strip():
            errors.append({"index": index, "issue": "missing_or_invalid_id"})
            continue
        if doc_id in seen_ids:
            errors.append({"index": index, "id": doc_id, "issue": "duplicate_id"})
        seen_ids.add(doc_id)

        if not isinstance(document, str) or not document.strip():
            errors.append({"index": index, "id": doc_id, "issue": "missing_or_empty_document"})

        if not isinstance(metadata, dict):
            errors.append({"index": index, "id": doc_id, "issue": "metadata_not_object"})
            continue

        missing_fields = [field for field in REQUIRED_METADATA_FIELDS if field not in metadata]
        if missing_fields:
            errors.append(
                {
                    "index": index,
                    "id": doc_id,
                    "issue": "missing_metadata_fields",
                    "fields": missing_fields,
                }
            )
            continue

        profile_id = metadata.get("profile_id")
        hangul = metadata.get("hangul")
        hanja = metadata.get("hanja")
        unicode_value = metadata.get("unicode")
        sound_meaning = metadata.get("sound_meaning")
        strokes = metadata.get("strokes")
        sound_ohaeng = metadata.get("sound_ohaeng")
        resource_ohaeng = metadata.get("resource_ohaeng")
        is_person_name_hanja = metadata.get("is_person_name_hanja")
        dataset = metadata.get("collection") or default_dataset
        source = metadata.get("source")
        item_type = metadata.get("type")

        row_errors: list[dict[str, Any]] = []

        if not isinstance(profile_id, str) or not profile_id.strip():
            row_errors.append({"issue": "invalid_profile_id"})
        else:
            expected_id = f"hanja_{profile_id}"
            if doc_id != expected_id:
                row_errors.append({"issue": "id_profile_id_mismatch", "expected": expected_id})
            if profile_id in seen_profile_ids:
                row_errors.append({"issue": "duplicate_profile_id"})
            seen_profile_ids.add(profile_id)

        for field_name, value in [
            ("hangul", hangul),
            ("hanja", hanja),
            ("unicode", unicode_value),
            ("sound_meaning", sound_meaning),
            ("dataset", dataset),
        ]:
            if not isinstance(value, str) or not value.strip():
                row_errors.append({"issue": f"invalid_{field_name}", "value": value})

        if not isinstance(strokes, int) or strokes <= 0:
            row_errors.append({"issue": "invalid_strokes", "value": strokes})

        for field_name, value in [
            ("sound_ohaeng", sound_ohaeng),
            ("resource_ohaeng", resource_ohaeng),
        ]:
            if value not in OHAENG_SET:
                row_errors.append({"issue": f"invalid_{field_name}", "value": value})

        if not isinstance(is_person_name_hanja, bool):
            row_errors.append({"issue": "invalid_is_person_name_hanja", "value": is_person_name_hanja})

        if row_errors:
            for error in row_errors:
                errors.append(
                    {
                        "index": index,
                        "id": doc_id,
                        "profile_id": profile_id,
                        "hanja": hanja,
                        "hangul": hangul,
                        **error,
                    }
                )
            continue

        unicode_to_profiles.setdefault(str(unicode_value), []).append(str(profile_id))
        sound_to_ohaeng.setdefault(str(hangul), set()).add(str(sound_ohaeng))

        rows.append(
            {
                "id": doc_id,
                "document": document,
                "dataset": str(dataset),
                "source": source,
                "type": item_type,
                "profile_id": profile_id,
                "hangul": hangul,
                "hanja": hanja,
                "unicode": unicode_value,
                "sound_meaning": sound_meaning,
                "strokes": strokes,
                "sound_ohaeng": sound_ohaeng,
                "resource_ohaeng": resource_ohaeng,
                "is_person_name_hanja": is_person_name_hanja,
            }
        )

    duplicate_unicodes = {
        unicode_value: profiles
        for unicode_value, profiles in unicode_to_profiles.items()
        if len(profiles) > 1
    }
    if duplicate_unicodes:
        warnings.append(
            {
                "issue": "duplicate_unicode_values",
                "count": len(duplicate_unicodes),
                "samples": dict(list(duplicate_unicodes.items())[:10]),
            }
        )

    inconsistent_sounds = {
        sound: sorted(values)
        for sound, values in sound_to_ohaeng.items()
        if len(values) > 1
    }
    if inconsistent_sounds:
        errors.append(
            {
                "issue": "same_hangul_has_multiple_sound_ohaeng",
                "count": len(inconsistent_sounds),
                "samples": dict(list(inconsistent_sounds.items())[:10]),
            }
        )

    return rows, errors, warnings


# 4. Neo4j 적재 계획 구성
# project_idea_naming.md의 노드/관계 설계를 실제 생성 수량으로 요약한다.
def build_plan(rows: list[dict[str, Any]], source_path: Path) -> dict[str, Any]:
    datasets = sorted({row["dataset"] for row in rows})
    permitted_count = sum(1 for row in rows if row["is_person_name_hanja"])

    return {
        "guide": PROJECT_GUIDE_PATH,
        "progress_checklist": PROGRESS_CHECKLIST_PATH,
        "source_of_truth": str(source_path),
        "raw_usage": "Raw files are validation references only; this script indexes the processed ChromaDB input.",
        "datasets": datasets,
        "nodes": {
            "Hanja": len(rows),
            "Sound": len({(row["dataset"], row["hangul"]) for row in rows}),
            "Stroke": len({(row["dataset"], row["strokes"]) for row in rows}),
            "Category:Ohaeng": len(datasets) * len(OHAENG_VALUES),
            "Law": len(datasets) if permitted_count else 0,
        },
        "relationships": {
            "HAS_SOUND": len(rows),
            "HAS_STROKES": len(rows),
            "BELONGS_TO_sound_ohaeng": len(rows),
            "BELONGS_TO_resource_ohaeng": len(rows),
            "PERMITTED_BY": permitted_count,
            "GENERATES": len(datasets) * len(OHAENG_GENERATES),
            "CONTROLS": len(datasets) * len(OHAENG_CONTROLS),
        },
    }


def print_plan(plan: dict[str, Any]) -> None:
    print("[Neo4j Hanja indexing plan]")
    print(f"- project guide: {plan['guide']}")
    print(f"- progress checklist: {plan['progress_checklist']}")
    print(f"- source of truth: {plan['source_of_truth']}")
    print(f"- raw usage: {plan['raw_usage']}")
    print(f"- datasets: {', '.join(plan['datasets']) if plan['datasets'] else '-'}")
    print("- nodes:")
    for name, count in plan["nodes"].items():
        print(f"  - {name}: {count}")
    print("- relationships:")
    for name, count in plan["relationships"].items():
        print(f"  - {name}: {count}")


# 5. Neo4j 스키마와 Cypher
# dataset을 키에 포함해 팀원이 만든 같은 이름의 노드와 충돌하지 않도록 한다.
CONSTRAINTS = [
    "CREATE CONSTRAINT hanja_dataset_profile_id IF NOT EXISTS FOR (h:Hanja) REQUIRE (h.dataset, h.profile_id) IS UNIQUE",
    "CREATE CONSTRAINT sound_dataset_hangul IF NOT EXISTS FOR (s:Sound) REQUIRE (s.dataset, s.hangul) IS UNIQUE",
    "CREATE CONSTRAINT stroke_dataset_count IF NOT EXISTS FOR (s:Stroke) REQUIRE (s.dataset, s.count) IS UNIQUE",
    "CREATE CONSTRAINT category_dataset_name IF NOT EXISTS FOR (c:Category) REQUIRE (c.dataset, c.name) IS UNIQUE",
    "CREATE CONSTRAINT law_dataset_law_id IF NOT EXISTS FOR (l:Law) REQUIRE (l.dataset, l.law_id) IS UNIQUE",
]

UPSERT_OHAENG_CATEGORIES = """
UNWIND $names AS name
MERGE (c:Category:Ohaeng {dataset: $dataset, name: name})
SET c.kind = 'ohaeng',
    c.updated_by = 'src/graph/index_hanja_neo4j.py'
"""

UPSERT_GENERATES_EDGES = """
UNWIND $edges AS edge
MERGE (from:Category:Ohaeng {dataset: $dataset, name: edge.from})
MERGE (to:Category:Ohaeng {dataset: $dataset, name: edge.to})
MERGE (from)-[r:GENERATES]->(to)
SET r.description = '상생 관계',
    r.updated_by = 'src/graph/index_hanja_neo4j.py'
"""

UPSERT_CONTROLS_EDGES = """
UNWIND $edges AS edge
MERGE (from:Category:Ohaeng {dataset: $dataset, name: edge.from})
MERGE (to:Category:Ohaeng {dataset: $dataset, name: edge.to})
MERGE (from)-[r:CONTROLS]->(to)
SET r.description = '상극 관계',
    r.updated_by = 'src/graph/index_hanja_neo4j.py'
"""

UPSERT_LAW_NODE = """
MERGE (law:Law {dataset: $dataset, law_id: $law.law_id})
SET law.name = $law.name,
    law.basis = $law.basis,
    law.description = $law.description,
    law.updated_by = 'src/graph/index_hanja_neo4j.py'
"""

UPSERT_HANJA_BATCH = """
UNWIND $rows AS row
MERGE (h:Hanja {dataset: row.dataset, profile_id: row.profile_id})
SET h.id = row.id,
    h.document = row.document,
    h.hanja = row.hanja,
    h.hangul = row.hangul,
    h.unicode = row.unicode,
    h.sound_meaning = row.sound_meaning,
    h.strokes = row.strokes,
    h.sound_ohaeng = row.sound_ohaeng,
    h.resource_ohaeng = row.resource_ohaeng,
    h.is_person_name_hanja = row.is_person_name_hanja,
    h.source = row.source,
    h.type = row.type,
    h.updated_by = 'src/graph/index_hanja_neo4j.py'
MERGE (sound:Sound {dataset: row.dataset, hangul: row.hangul})
SET sound.ohaeng = row.sound_ohaeng,
    sound.updated_by = 'src/graph/index_hanja_neo4j.py'
MERGE (stroke:Stroke {dataset: row.dataset, count: row.strokes})
SET stroke.unit = '획',
    stroke.method = 'kKangXi',
    stroke.updated_by = 'src/graph/index_hanja_neo4j.py'
MERGE (sound_cat:Category:Ohaeng {dataset: row.dataset, name: row.sound_ohaeng})
MERGE (resource_cat:Category:Ohaeng {dataset: row.dataset, name: row.resource_ohaeng})
MERGE (h)-[:HAS_SOUND]->(sound)
MERGE (h)-[:HAS_STROKES]->(stroke)
WITH h, row, sound_cat, resource_cat
OPTIONAL MATCH (h)-[old_sound_rel:BELONGS_TO {kind: 'sound_ohaeng'}]->(old_sound_cat:Category:Ohaeng {dataset: row.dataset})
WHERE old_sound_cat.name <> row.sound_ohaeng
WITH h, row, sound_cat, resource_cat, collect(old_sound_rel) AS old_sound_rels
FOREACH (rel IN old_sound_rels | DELETE rel)
WITH h, row, sound_cat, resource_cat
OPTIONAL MATCH (h)-[old_resource_rel:BELONGS_TO {kind: 'resource_ohaeng'}]->(old_resource_cat:Category:Ohaeng {dataset: row.dataset})
WHERE old_resource_cat.name <> row.resource_ohaeng
WITH h, row, sound_cat, resource_cat, collect(old_resource_rel) AS old_resource_rels
FOREACH (rel IN old_resource_rels | DELETE rel)
WITH h, row, sound_cat, resource_cat
MERGE (h)-[sound_rel:BELONGS_TO {kind: 'sound_ohaeng'}]->(sound_cat)
SET sound_rel.source_field = 'sound_ohaeng',
    sound_rel.updated_by = 'src/graph/index_hanja_neo4j.py'
MERGE (h)-[resource_rel:BELONGS_TO {kind: 'resource_ohaeng'}]->(resource_cat)
SET resource_rel.source_field = 'resource_ohaeng',
    resource_rel.updated_by = 'src/graph/index_hanja_neo4j.py'
WITH h, row
MATCH (law:Law {dataset: row.dataset, law_id: $law_id})
FOREACH (_ IN CASE WHEN row.is_person_name_hanja THEN [1] ELSE [] END |
  MERGE (h)-[permit:PERMITTED_BY]->(law)
  SET permit.basis = 'metadata.is_person_name_hanja',
      permit.updated_by = 'src/graph/index_hanja_neo4j.py'
)
"""

VALIDATE_COUNTS = """
MATCH (h:Hanja {dataset: $dataset})
WITH count(h) AS hanja_count
MATCH (c:Category:Ohaeng {dataset: $dataset})
WITH hanja_count, count(c) AS category_count
MATCH (:Category:Ohaeng {dataset: $dataset})-[g:GENERATES]->(:Category:Ohaeng {dataset: $dataset})
WITH hanja_count, category_count, count(g) AS generates_count
MATCH (:Category:Ohaeng {dataset: $dataset})-[c:CONTROLS]->(:Category:Ohaeng {dataset: $dataset})
RETURN hanja_count, category_count, generates_count, count(c) AS controls_count
"""


# 6. Neo4j 쓰기 함수
# 기본 실행은 dry-run이며 --execute가 있을 때만 실제 DB에 MERGE 한다.
def batched(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [rows[start : start + batch_size] for start in range(0, len(rows), batch_size)]


def require_neo4j_driver() -> None:
    if GraphDatabase is None:
        raise RuntimeError(f"neo4j package import failed: {NEO4J_IMPORT_ERROR}")


def execute_neo4j(
    *,
    rows: list[dict[str, Any]],
    uri: str,
    user: str,
    password: str,
    database: str,
    batch_size: int,
) -> None:
    require_neo4j_driver()
    datasets = sorted({row["dataset"] for row in rows})
    generates_rows = [{"from": src, "to": dst} for src, dst in OHAENG_GENERATES]
    controls_rows = [{"from": src, "to": dst} for src, dst in OHAENG_CONTROLS]

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            for statement in CONSTRAINTS:
                session.run(statement)

            for dataset in datasets:
                session.run(UPSERT_OHAENG_CATEGORIES, dataset=dataset, names=OHAENG_VALUES)
                session.run(UPSERT_GENERATES_EDGES, dataset=dataset, edges=generates_rows)
                session.run(UPSERT_CONTROLS_EDGES, dataset=dataset, edges=controls_rows)
                session.run(UPSERT_LAW_NODE, dataset=dataset, law=LAW_NODE)

            total = len(rows)
            for index, batch in enumerate(batched(rows, batch_size), 1):
                session.run(UPSERT_HANJA_BATCH, rows=batch, law_id=LAW_NODE["law_id"])
                written = min(index * batch_size, total)
                print(f"Indexed {written}/{total} Hanja nodes into Neo4j.")

            for dataset in datasets:
                result = session.run(VALIDATE_COUNTS, dataset=dataset).single()
                if result:
                    print(
                        f"[{dataset}] Hanja={result['hanja_count']}, "
                        f"Category={result['category_count']}, "
                        f"GENERATES={result['generates_count']}, "
                        f"CONTROLS={result['controls_count']}"
                    )
    finally:
        driver.close()


def check_connection(uri: str, user: str, password: str, database: str) -> None:
    require_neo4j_driver()
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            value = session.run("RETURN 1 AS ok").single()
            if value is None or value["ok"] != 1:
                raise RuntimeError("Neo4j connection check returned an unexpected result.")
    finally:
        driver.close()


# 7. CLI 실행 흐름
# dry-run 검증 → 사용자 확인 → --execute 실제 적재 순서로 쓰기 위해 옵션을 분리한다.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the project Hanja Neo4j graph index.")
    parser.add_argument("--source", help="Path to hanja_documents.json. Defaults to NEO4J_HANJA_SOURCE or data/processed/hanja_documents.json.")
    parser.add_argument("--dataset", help="Fallback dataset name. Defaults to NEO4J_HANJA_DATASET or hanja_profiles.")
    parser.add_argument("--database", help="Neo4j database name. Defaults to NEO4J_DATABASE or neo4j.")
    parser.add_argument("--batch-size", type=int, help="Neo4j write batch size. Defaults to NEO4J_BATCH_SIZE or 500.")
    parser.add_argument("--check-connection", action="store_true", help="Validate Neo4j credentials without writing data.")
    parser.add_argument("--execute", action="store_true", help="Write data to Neo4j. Omit for dry-run validation only.")
    parser.add_argument("--max-issues", type=int, default=20, help="Maximum validation issues to print.")
    return parser.parse_args()


def print_issues(title: str, issues: list[dict[str, Any]], max_issues: int) -> None:
    if not issues:
        return
    print(title)
    for issue in issues[:max_issues]:
        print(f"- {json.dumps(issue, ensure_ascii=False)}")
    remaining = len(issues) - max_issues
    if remaining > 0:
        print(f"- ... {remaining} more issue(s)")


def main() -> None:
    configure_stdout()
    root = repo_root()
    load_project_env(root)
    args = parse_args()

    source_value = args.source or os.getenv("NEO4J_HANJA_SOURCE") or DEFAULT_SOURCE
    dataset = args.dataset or os.getenv("NEO4J_HANJA_DATASET") or DEFAULT_DATASET
    database = args.database or os.getenv("NEO4J_DATABASE") or DEFAULT_DATABASE
    batch_size = args.batch_size or env_int("NEO4J_BATCH_SIZE", DEFAULT_BATCH_SIZE)
    source_path = resolve_path(root, source_value)

    print(f"Project guide: {root / PROJECT_GUIDE_PATH}")
    print(f"Loading source JSON: {source_path}")

    records = read_json(source_path)
    rows, errors, warnings = validate_records(records, dataset)

    print(f"Source records: {len(records) if isinstance(records, list) else 0}")
    print(f"Validated rows: {len(rows)}")

    print_issues("[warnings]", warnings, args.max_issues)
    if errors:
        print_issues("[validation errors]", errors, args.max_issues)
        raise SystemExit("Neo4j indexing stopped before DB access because source validation failed.")

    plan = build_plan(rows, source_path)
    print_plan(plan)

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")

    if args.check_connection or args.execute:
        missing_env = [
            name
            for name, value in [
                ("NEO4J_URI", uri),
                ("NEO4J_USER", user),
                ("NEO4J_PASSWORD", password),
            ]
            if not value
        ]
        if missing_env:
            raise SystemExit(f"Missing Neo4j environment value(s): {', '.join(missing_env)}")

        print(f"Checking Neo4j connection: database={database}, uri_set={bool(uri)}, user_set={bool(user)}")
        try:
            check_connection(str(uri), str(user), str(password), database)
        except Exception as error:
            raise SystemExit(
                "Neo4j connection check failed before any write.\n"
                f"- error: {type(error).__name__}: {error}\n"
                "- likely reason: Neo4j is not running, the Bolt port is closed, or NEO4J_URI points to the wrong host.\n"
                "- action: start Neo4j and rerun with --check-connection before using --execute."
            ) from None
        print("Neo4j connection check passed.")

    if not args.execute:
        print("Dry-run completed. No Neo4j data was written. Use --execute after review to write data.")
        return

    execute_neo4j(
        rows=rows,
        uri=str(uri),
        user=str(user),
        password=str(password),
        database=database,
        batch_size=batch_size,
    )
    print("Neo4j Hanja indexing completed.")


if __name__ == "__main__":
    main()
