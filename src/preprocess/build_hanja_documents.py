"""Build Chroma-ready Hanja documents from the verified Hanja profile JSON.

Input:
    data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json

Outputs:
    data/processed/hanja_documents.json
    data/processed/hanja_documents_validation.json

The source profile JSON is treated as read-only. This script only creates
document-oriented derivatives for vector indexing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = [
    "profile_id",
    "hangul",
    "hanja",
    "unicode",
    "sound_meaning",
    "strokes",
    "sound_ohaeng",
    "resource_ohaeng",
]

DEFAULT_SOURCE = Path(
    "data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json"
)
DEFAULT_OUTPUT = Path("data/processed/hanja_documents.json")
DEFAULT_VALIDATION = Path("data/processed/hanja_documents_validation.json")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def validate_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_profile_ids: set[str] = set()

    for index, row in enumerate(rows, start=1):
        profile_id = row.get("profile_id", f"row_{index}")

        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "missing_required_fields",
                    "fields": missing,
                }
            )
            continue

        extra = [field for field in row.keys() if field not in REQUIRED_FIELDS]
        if extra:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "unexpected_fields",
                    "fields": extra,
                }
            )

        if profile_id in seen_profile_ids:
            issues.append({"profile_id": profile_id, "issue": "duplicate_profile_id"})
        seen_profile_ids.add(profile_id)

        expected_profile_id = f"OHE-{index:05d}"
        if profile_id != expected_profile_id:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "profile_id_sequence_mismatch",
                    "expected": expected_profile_id,
                }
            )

        hanja = str(row["hanja"])
        unicode_value = str(row["unicode"])
        expected_unicode = f"U+{ord(hanja):04X}" if len(hanja) == 1 else None
        if len(hanja) != 1:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "hanja_must_be_single_character",
                    "value": hanja,
                }
            )
        elif unicode_value != expected_unicode:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "unicode_mismatch",
                    "value": unicode_value,
                    "expected": expected_unicode,
                }
            )

        if len(str(row["hangul"])) != 1:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "hangul_must_be_single_syllable",
                    "value": row["hangul"],
                }
            )

        if not str(row["sound_meaning"]).strip():
            issues.append({"profile_id": profile_id, "issue": "empty_sound_meaning"})

        if not isinstance(row["strokes"], int):
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "strokes_must_be_integer",
                    "value": row["strokes"],
                }
            )

    return issues


def build_document_text(row: dict[str, Any]) -> str:
    return (
        f"한자 {row['hanja']}({row['hangul']})은 뜻이 '{row['sound_meaning']}'인 "
        f"인명용 한자이다. 유니코드는 {row['unicode']}, 획수는 {row['strokes']}획, "
        f"발음오행은 {row['sound_ohaeng']}, 자원오행은 {row['resource_ohaeng']}이다."
    )


def build_hanja_document(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"hanja_{row['profile_id']}",
        "document": build_document_text(row),
        "metadata": {
            "type": "hanja",
            "collection": "hanja_profiles",
            "source": "hanja_unicode_ohaeng_verified_corrected.json",
            "profile_id": row["profile_id"],
            "hangul": row["hangul"],
            "hanja": row["hanja"],
            "unicode": row["unicode"],
            "sound_meaning": row["sound_meaning"],
            "strokes": row["strokes"],
            "sound_ohaeng": row["sound_ohaeng"],
            "resource_ohaeng": row["resource_ohaeng"],
            "is_person_name_hanja": True,
        },
    }


def validate_documents(
    source_rows: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if len(source_rows) != len(documents):
        issues.append(
            {
                "issue": "document_count_mismatch",
                "source_count": len(source_rows),
                "document_count": len(documents),
            }
        )

    for row, doc in zip(source_rows, documents):
        doc_id = doc.get("id")
        profile_id = row["profile_id"]
        expected_id = f"hanja_{profile_id}"

        if doc_id != expected_id:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "id_mismatch",
                    "value": doc_id,
                    "expected": expected_id,
                }
            )

        if doc_id in seen_ids:
            issues.append({"id": doc_id, "issue": "duplicate_document_id"})
        seen_ids.add(str(doc_id))

        if not doc.get("document"):
            issues.append({"profile_id": profile_id, "issue": "empty_document"})

        metadata = doc.get("metadata")
        if not isinstance(metadata, dict):
            issues.append({"profile_id": profile_id, "issue": "metadata_not_object"})
            continue

        for field in REQUIRED_FIELDS:
            if metadata.get(field) != row[field]:
                issues.append(
                    {
                        "profile_id": profile_id,
                        "issue": "metadata_value_mismatch",
                        "field": field,
                        "value": metadata.get(field),
                        "expected": row[field],
                    }
                )

        required_terms = [
            str(row["hanja"]),
            str(row["hangul"]),
            str(row["sound_meaning"]),
            str(row["unicode"]),
            str(row["strokes"]),
            str(row["sound_ohaeng"]),
            str(row["resource_ohaeng"]),
        ]
        missing_terms = [term for term in required_terms if term not in doc["document"]]
        if missing_terms:
            issues.append(
                {
                    "profile_id": profile_id,
                    "issue": "document_missing_required_terms",
                    "terms": missing_terms,
                }
            )

    return issues


def build_documents(source_path: Path, output_path: Path, validation_path: Path) -> dict[str, Any]:
    rows = read_json(source_path)
    if not isinstance(rows, list):
        raise TypeError(f"Source JSON must contain a list: {source_path}")

    source_issues = validate_source_rows(rows)
    if source_issues:
        all_issues = source_issues
        validation = {
            "source": str(source_path),
            "output": str(output_path),
            "status": "failed",
            "ok": False,
            "source_rows": len(rows),
            "document_rows": 0,
            "source_count": len(rows),
            "document_count": 0,
            "issue_count": len(all_issues),
            "issues": all_issues,
            "source_issues": source_issues,
            "document_issues": [],
        }
        write_json(validation_path, validation)
        raise ValueError(
            f"Source validation failed with {len(source_issues)} issue(s). "
            f"See {validation_path}"
        )

    documents = [build_hanja_document(row) for row in rows]
    document_issues = validate_documents(rows, documents)
    all_issues = source_issues + document_issues
    ok = not all_issues

    validation = {
        "source": str(source_path),
        "output": str(output_path),
        "status": "ok" if ok else "failed",
        "ok": ok,
        "source_rows": len(rows),
        "document_rows": len(documents),
        "source_count": len(rows),
        "document_count": len(documents),
        "issue_count": len(all_issues),
        "issues": all_issues,
        "source_issues": source_issues,
        "document_issues": document_issues,
        "sample_ids": [doc["id"] for doc in documents[:5]],
    }
    write_json(validation_path, validation)

    if document_issues:
        raise ValueError(
            f"Document validation failed with {len(document_issues)} issue(s). "
            f"See {validation_path}"
        )

    write_json(output_path, documents)
    return validation


def resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else repo_root() / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert verified Hanja profiles into Chroma-ready documents."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = resolve_project_path(args.source)
    output = resolve_project_path(args.output)
    validation_path = resolve_project_path(args.validation)

    validation = build_documents(source, output, validation_path)
    print("[ok] built Hanja documents")
    print(f"  source: {source}")
    print(f"  output: {output}")
    print(f"  validation: {validation_path}")
    print(f"  rows: {validation['document_rows']}")


if __name__ == "__main__":
    main()
