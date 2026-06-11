"""Index Hanja document/metadata records into the project ChromaDB store.

This script follows the existing team ChromaDB structure:
- storage path: data/chroma
- collection name: hanja_col
- embedding model: jhgan/ko-sroberta-multitask
"""

import json
import sys
import traceback
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction


# 1. 기본 설정
# 팀 공통 ChromaDB 구조에 맞춰 모델명, 컬렉션명, 배치 크기를 고정한다.
MODEL_NAME = "jhgan/ko-sroberta-multitask"
COLLECTION_NAME = "hanja_col"
BATCH_SIZE = 500

REQUIRED_METADATA_FIELDS = [
    "profile_id",
    "hangul",
    "hanja",
    "unicode",
    "sound_meaning",
    "strokes",
    "sound_ohaeng",
    "resource_ohaeng",
]


# 2. 콘솔, 경로, 파일 입출력
# Windows 콘솔 인코딩 문제를 줄이고, repo 기준 경로에서 JSON 파일을 읽고 쓴다.
def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 3. 실패 원인 진단 및 TXT 에러 리포트
# 실패 시 JSON 검증 파일 대신 사람이 바로 읽을 수 있는 원인/조치 TXT를 남긴다.
def diagnose_error(error: BaseException, chroma_path: Path) -> list[str]:
    message = str(error).lower()
    if "disk i/o error" in message:
        return [
            "ChromaDB는 PersistentClient를 만들 때 내부적으로 SQLite DB를 생성합니다.",
            f"이번 오류는 SQLite가 '{chroma_path}' 경로에 DB/journal 파일을 쓰거나 정리하는 중 실패했다는 뜻입니다.",
            "입력 JSON 검증은 이미 통과했으므로 hanja_documents.json 구조 문제로 보기는 어렵습니다.",
            "가능성이 높은 원인은 실패한 이전 실행에서 남은 chroma.sqlite3-journal 파일, 파일 잠금, 또는 현재 실행 환경의 파일 삭제/쓰기 권한 제한입니다.",
            "data/chroma 폴더의 부분 생성물을 정리한 뒤 skn29-3rd 환경에서 다시 실행하는 것이 우선 조치입니다.",
        ]

    if "permission" in message or "access is denied" in message or "액세스가 거부" in message:
        return [
            "현재 실행 환경에서 파일 또는 폴더 접근 권한이 부족합니다.",
            "ChromaDB 저장 경로, 모델 캐시 경로, 또는 이전 실행에서 생성된 파일이 잠겨 있을 수 있습니다.",
            "열려 있는 Python/VSCode/Jupyter 프로세스를 종료하고 권한이 있는 터미널에서 다시 실행해야 합니다.",
        ]

    if "huggingface" in message or "connection" in message or "download" in message:
        return [
            f"임베딩 모델 '{MODEL_NAME}' 로드 중 네트워크 또는 모델 캐시 문제가 발생했을 가능성이 있습니다.",
            "팀 기준 모델을 사용하려면 로컬 캐시가 있거나 HuggingFace 접근이 가능해야 합니다.",
        ]

    return [
        "입력 데이터 검증 이후 ChromaDB 인덱싱 과정에서 예외가 발생했습니다.",
        "아래 traceback의 마지막 줄과 실패 단계를 기준으로 원인을 확인해야 합니다.",
    ]


def write_error_report(
    *,
    path: Path,
    stage: str,
    error: BaseException,
    source_path: Path,
    chroma_path: Path,
    source_count: int,
    sample_ids: list[str],
    issues: list[dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Hanja ChromaDB Indexing Error Report",
        "=" * 40,
        "",
        f"stage: {stage}",
        f"source: {source_path}",
        f"chroma_path: {chroma_path}",
        f"collection: {COLLECTION_NAME}",
        f"embedding_model: {MODEL_NAME}",
        f"batch_size: {BATCH_SIZE}",
        f"source_count: {source_count}",
        f"sample_ids: {', '.join(sample_ids) if sample_ids else '-'}",
        "",
        "error:",
        f"{type(error).__name__}: {error}",
        "",
        "likely_reason:",
    ]
    lines.extend(f"- {line}" for line in diagnose_error(error, chroma_path))

    if issues:
        lines.extend(["", "validation_issues:"])
        for issue in issues[:30]:
            lines.append(f"- {json.dumps(issue, ensure_ascii=False)}")
        if len(issues) > 30:
            lines.append(f"- ... {len(issues) - 30} more issue(s)")

    lines.extend(["", "traceback:", traceback.format_exc()])
    path.write_text("\n".join(lines), encoding="utf-8")


# 4. 입력 document/metadata 구조 검증
# hanja_documents.json이 ChromaDB 적재에 필요한 id/document/metadata 구조인지 확인한다.
def validate_records(records: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    if not isinstance(records, list):
        return [{"issue": "source_json_must_be_list"}]

    seen_ids: set[str] = set()
    for index, item in enumerate(records, 1):
        if not isinstance(item, dict):
            issues.append({"index": index, "issue": "item_not_object"})
            continue

        doc_id = item.get("id")
        document = item.get("document")
        metadata = item.get("metadata")

        if not isinstance(doc_id, str) or not doc_id:
            issues.append({"index": index, "issue": "missing_or_invalid_id"})
        elif doc_id in seen_ids:
            issues.append({"index": index, "id": doc_id, "issue": "duplicate_id"})
        else:
            seen_ids.add(doc_id)

        if not isinstance(document, str) or not document.strip():
            issues.append({"index": index, "id": doc_id, "issue": "missing_or_empty_document"})

        if not isinstance(metadata, dict):
            issues.append({"index": index, "id": doc_id, "issue": "metadata_not_object"})
            continue

        missing_fields = [field for field in REQUIRED_METADATA_FIELDS if field not in metadata]
        if missing_fields:
            issues.append(
                {
                    "index": index,
                    "id": doc_id,
                    "issue": "missing_metadata_fields",
                    "fields": missing_fields,
                }
            )

        profile_id = metadata.get("profile_id")
        if isinstance(profile_id, str) and isinstance(doc_id, str):
            expected_id = f"hanja_{profile_id}"
            if doc_id != expected_id:
                issues.append(
                    {
                        "index": index,
                        "id": doc_id,
                        "issue": "id_profile_id_mismatch",
                        "expected": expected_id,
                    }
                )

    return issues


# 5. 성공 검증 리포트 구성
# 인덱싱이 정상 완료된 경우 적재 수량과 샘플 ID를 JSON 리포트로 남긴다.
def build_validation_report(
    *,
    status: str,
    source_path: Path,
    chroma_path: Path,
    collection_name: str,
    source_count: int,
    collection_count: int,
    issues: list[dict[str, Any]],
    sample_ids: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": status == "ok",
        "source": str(source_path),
        "chroma_path": str(chroma_path),
        "collection": collection_name,
        "embedding_model": MODEL_NAME,
        "batch_size": BATCH_SIZE,
        "source_count": source_count,
        "collection_count": collection_count,
        "issue_count": len(issues),
        "issues": issues,
        "sample_ids": sample_ids,
    }


# 6. ChromaDB 컬렉션 재생성 및 배치 적재
# 기존 hanja_col은 삭제 후 새로 만들고, 500건 단위로 문서를 추가한다.
def recreate_collection(client: chromadb.ClientAPI, collection_name: str, embedding_fn: Any):
    try:
        client.delete_collection(collection_name)
        print(f"Deleted existing '{collection_name}' collection.")
    except Exception:
        pass

    return client.create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"description": "Hanja sound, meaning, stroke, and ohaeng documents"},
    )


def add_batches(collection: Any, records: list[dict[str, Any]]) -> None:
    total = len(records)
    for start in range(0, total, BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]
        collection.add(
            ids=[item["id"] for item in batch],
            documents=[item["document"] for item in batch],
            metadatas=[item["metadata"] for item in batch],
        )
        print(f"Indexed {min(start + BATCH_SIZE, total)}/{total} documents.")


# 7. ChromaDB 적재 결과 검증
# 컬렉션 count와 대표 샘플 ID 조회로 실제 저장 여부를 확인한다.
def validate_collection(collection: Any, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected_count = len(records)
    actual_count = collection.count()

    if actual_count != expected_count:
        issues.append(
            {
                "issue": "collection_count_mismatch",
                "expected": expected_count,
                "actual": actual_count,
            }
        )

    sample_ids = [records[0]["id"], records[-1]["id"]] if records else []
    if sample_ids:
        result = collection.get(ids=sample_ids)
        found_ids = set(result.get("ids", []))
        missing_ids = [doc_id for doc_id in sample_ids if doc_id not in found_ids]
        if missing_ids:
            issues.append({"issue": "sample_ids_not_found", "ids": missing_ids})

    return issues


# 8. 전체 실행 흐름
# 입력 검증, ChromaDB 초기화, 임베딩 모델 로드, 적재, 결과 검증을 순서대로 수행한다.
def main() -> None:
    configure_stdout()

    root = repo_root()
    source_path = root / "data" / "processed" / "hanja_documents.json"
    validation_path = root / "data" / "processed" / "hanja_chromadb_index_validation.json"
    error_report_path = root / "data" / "processed" / "hanja_chromadb_error_report.txt"
    chroma_path = root / "data" / "chroma"

    print(f"Loading source JSON: {source_path}")
    records = read_json(source_path)
    issues = validate_records(records)
    source_count = len(records) if isinstance(records, list) else 0
    sample_ids = [records[0]["id"], records[-1]["id"]] if isinstance(records, list) and records else []

    if issues:
        error = ValueError(f"Source validation failed with {len(issues)} issue(s).")
        write_error_report(
            path=error_report_path,
            stage="source_validation",
            error=error,
            source_path=source_path,
            chroma_path=chroma_path,
            source_count=source_count,
            sample_ids=sample_ids,
            issues=issues,
        )
        raise SystemExit(f"Source validation failed. See {error_report_path}")

    stage = "initialize_chromadb_client"
    try:
        print(f"Initializing ChromaDB persistent client at {chroma_path}...")
        client = chromadb.PersistentClient(path=str(chroma_path))

        stage = "load_embedding_model"
        print(f"Loading embedding model: {MODEL_NAME}")
        print("If the model is not cached locally, sentence-transformers may try to download it.")
        embedding_fn = SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)

        stage = "recreate_collection"
        collection = recreate_collection(client, COLLECTION_NAME, embedding_fn)

        stage = "add_documents"
        print(f"Indexing '{COLLECTION_NAME}' ({source_count} documents)...")
        add_batches(collection, records)

        stage = "validate_collection"
        collection_issues = validate_collection(collection, records)
        collection_count = collection.count()
    except Exception as error:
        write_error_report(
            path=error_report_path,
            stage=stage,
            error=error,
            source_path=source_path,
            chroma_path=chroma_path,
            source_count=source_count,
            sample_ids=sample_ids,
        )
        raise SystemExit(f"ChromaDB indexing failed. See {error_report_path}") from error

    report = build_validation_report(
        status="ok" if not collection_issues else "failed",
        source_path=source_path,
        chroma_path=chroma_path,
        collection_name=COLLECTION_NAME,
        source_count=source_count,
        collection_count=collection_count,
        issues=collection_issues,
        sample_ids=sample_ids,
    )
    write_json(validation_path, report)

    if collection_issues:
        error = ValueError(f"ChromaDB validation failed with {len(collection_issues)} issue(s).")
        write_error_report(
            path=error_report_path,
            stage="validate_collection",
            error=error,
            source_path=source_path,
            chroma_path=chroma_path,
            source_count=source_count,
            sample_ids=sample_ids,
            issues=collection_issues,
        )
        raise SystemExit(f"ChromaDB validation failed. See {error_report_path}")

    print(f"{COLLECTION_NAME} indexing completed.")
    print(f"Collection count: {collection_count}")
    print(f"Validation written: {validation_path}")


if __name__ == "__main__":
    main()
