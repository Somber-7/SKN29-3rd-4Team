"""
graph_server.py - Neo4j graph MCP server (graph_db node)

This server exposes read-only tools for the Hanja graph created by
src/graph/index_hanja_neo4j.py.

[Graph source]
  data/processed/hanja_documents.json

[Neo4j graph]
  Hanja, Sound, Stroke, Category:Ohaeng, Law
  HAS_SOUND, HAS_STROKES, BELONGS_TO, PERMITTED_BY, GENERATES, CONTROLS

[Tool list]
  1. check_graph_status          - Neo4j indexing count check
  2. lookup_hanja                - Hanja lookup by profile_id/hanja/hangul
  3. check_person_name_hanja     - Person-name Hanja permission check
  4. get_ohaeng_relations        - Ohaeng generates/controls traversal
  5. recommend_hanja_by_ohaeng   - Hanja filtering by ohaeng/strokes/sound/meaning
  6. answer_graph_query          - Natural-language router for graph queries

Importing this module does not connect to Neo4j. Connections are opened only
inside tool calls, so local syntax/self checks can run before server access.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from neo4j import GraphDatabase


mcp = FastMCP("GraphServer")


# 1. Project paths and constants
# Keep these aligned with docs/프로젝트_아이디어_작명.md and src/graph/index_hanja_neo4j.py.
BASE_DIR = Path(__file__).resolve().parents[2]
SOURCE_PATH = BASE_DIR / "data" / "processed" / "hanja_documents.json"

# Loading .env here only reads local configuration; it does not open Neo4j.
load_dotenv(BASE_DIR / ".env")
DATASET = os.getenv("NEO4J_HANJA_DATASET", "hanja_profiles")

OHAENG_KO = ["목", "화", "토", "금", "수"]
OHAENG_HANJA_TO_KO = {
    "木": "목",
    "火": "화",
    "土": "토",
    "金": "금",
    "水": "수",
}
OHAENG_KO_TO_HANJA = {value: key for key, value in OHAENG_HANJA_TO_KO.items()}

EXPECTED_RELATIONSHIPS = {
    "HAS_SOUND": 2420,
    "HAS_STROKES": 2420,
    "BELONGS_TO": 4840,
    "PERMITTED_BY": 2420,
    "GENERATES": 5,
    "CONTROLS": 5,
}


# 2. Console, env, and Neo4j connection utilities
# Env values are loaded lazily and secrets are never printed by these tools.
def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _load_env() -> None:
    load_dotenv(BASE_DIR / ".env")


def _neo4j_config() -> dict[str, str]:
    _load_env()
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE") or "neo4j"

    missing = [
        name
        for name, value in [
            ("NEO4J_URI", uri),
            ("NEO4J_USER", user),
            ("NEO4J_PASSWORD", password),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Neo4j environment value(s): {', '.join(missing)}")

    return {
        "uri": str(uri),
        "user": str(user),
        "password": str(password),
        "database": str(database),
    }


def _run_read(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = _neo4j_config()
    driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))
    try:
        driver.verify_connectivity()
        with driver.session(database=config["database"]) as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]
    finally:
        driver.close()


def _safe_limit(limit: int, default: int = 10, maximum: int = 30) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _normalize_ohaeng(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    value = value.replace("金", "金")
    if value in OHAENG_KO:
        return value
    return OHAENG_HANJA_TO_KO.get(value)


def _format_hanja_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "[결과 없음] 조건에 맞는 한자를 찾지 못했습니다."

    lines = []
    for index, row in enumerate(rows, 1):
        lines.append(
            f"  [{index}] {row.get('hanja')}({row.get('hangul')}) "
            f"{row.get('sound_meaning')} / {row.get('strokes')}획 / "
            f"발음오행 {row.get('sound_ohaeng')} / 자원오행 {row.get('resource_ohaeng')} / "
            f"profile_id {row.get('profile_id')}"
        )
    return "\n".join(lines)


def _format_neo4j_error(error: Exception) -> str:
    return (
        f"[오류] Neo4j 조회 실패: {type(error).__name__}: {error}\n"
        "확인할 것: Docker Neo4j 실행 여부, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE"
    )


def _extract_profile_id(query: str) -> str | None:
    match = re.search(r"\bOHE-\d{5}\b", query, flags=re.IGNORECASE)
    return match.group(0).upper() if match else None


def _extract_hanja_chars(query: str) -> list[str]:
    chars = re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", query)
    return [char.replace("金", "金") for char in chars]


def _extract_ohaeng(query: str) -> str | None:
    for value in OHAENG_KO:
        if value in query:
            return value
    for value in OHAENG_HANJA_TO_KO:
        if value in query:
            return OHAENG_HANJA_TO_KO[value]
    return None


def _extract_strokes(query: str) -> int | None:
    match = re.search(r"(\d{1,2})\s*획", query)
    return int(match.group(1)) if match else None


def _extract_hangul_sound(query: str) -> str | None:
    cleaned_query = query.replace("발음오행", " ").replace("자원오행", " ").replace("오행", " ")
    patterns = [
        r"(?:음|독음|발음|한글\s*음)\s*(?:은|는|이|가|:)?\s*([가-힣])",
        r"([가-힣])\s*(?:음|독음)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned_query)
        if match:
            return match.group(1)
    return None


def _normalize_meaning_keyword(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip(" '\"“”‘’.,")
    for suffix in ["스러운", "로운", "하는", "하다", "한", "은", "는", "인", "의"]:
        if len(value) > len(suffix) and value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value or None


def _extract_meaning_keyword(query: str) -> str | None:
    patterns = [
        r"([가-힣]{1,8})\s*(?:뜻|의미)",
        r"(?:뜻|의미)\s*(?:이|가|은|는|으로|와|과|:)?\s*['\"“”‘’]?([가-힣]{1,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            keyword = _normalize_meaning_keyword(match.group(1))
            if keyword:
                return keyword

    for keyword in ["밝", "지혜", "슬기", "맑", "복", "귀", "강", "아름", "평안", "총명", "빛"]:
        if keyword in query:
            return keyword
    return None


def _classify_graph_query(query: str, limit: int = 10) -> dict[str, Any]:
    normalized_query = str(query or "").strip()
    profile_id = _extract_profile_id(normalized_query)
    hanja_chars = _extract_hanja_chars(normalized_query)
    ohaeng = _extract_ohaeng(normalized_query)
    strokes = _extract_strokes(normalized_query)
    hangul = _extract_hangul_sound(normalized_query)
    meaning_keyword = _extract_meaning_keyword(normalized_query)

    status_keywords = {"상태", "현황", "카운트", "건수", "count", "검증", "적재"}
    permission_keywords = {"인명용", "허용", "출생신고", "등록", "가능한", "가능해", "쓸 수"}
    relation_keywords = {"상생", "상극", "관계", "생", "극"}
    recommend_keywords = {"추천", "찾", "골라", "후보", "조건", "뜻", "의미", "획", "오행"}

    if any(keyword in normalized_query for keyword in status_keywords) and not hanja_chars and not profile_id:
        return {"tool": "check_graph_status", "params": {}, "reason": "Neo4j 그래프 적재 상태 확인"}

    if any(keyword in normalized_query for keyword in permission_keywords):
        if hanja_chars:
            return {
                "tool": "check_person_name_hanja",
                "params": {"hanja": hanja_chars[0]},
                "reason": "인명용 한자 허용 여부 확인",
            }
        return {
            "tool": "needs_hanja_for_permission",
            "params": {},
            "reason": "인명용 한자 검증에는 한자 문자가 필요함",
        }

    if ohaeng and any(keyword in normalized_query for keyword in relation_keywords) and "추천" not in normalized_query:
        return {
            "tool": "get_ohaeng_relations",
            "params": {"element": ohaeng},
            "reason": "오행 상생/상극 관계 조회",
        }

    if (
        any(keyword in normalized_query for keyword in recommend_keywords)
        and (ohaeng or strokes or hangul or meaning_keyword)
    ):
        sound_ohaeng = ohaeng if "발음" in normalized_query else None
        resource_ohaeng = ohaeng if sound_ohaeng is None else None
        return {
            "tool": "recommend_hanja_by_ohaeng",
            "params": {
                "sound_ohaeng": sound_ohaeng,
                "resource_ohaeng": resource_ohaeng,
                "hangul": hangul,
                "meaning_keyword": meaning_keyword,
                "strokes": strokes,
                "limit": limit,
            },
            "reason": "오행/뜻/획수/음 조건 기반 한자 추천",
        }

    if profile_id:
        return {
            "tool": "lookup_hanja",
            "params": {"profile_id": profile_id, "limit": limit},
            "reason": "profile_id 기준 한자 조회",
        }

    if hanja_chars:
        return {
            "tool": "lookup_hanja",
            "params": {"hanja": hanja_chars[0], "limit": limit},
            "reason": "한자 문자 기준 조회",
        }

    if hangul:
        return {
            "tool": "lookup_hanja",
            "params": {"hangul": hangul, "limit": limit},
            "reason": "한글 음 기준 한자 조회",
        }

    return {
        "tool": "unsupported",
        "params": {},
        "reason": "Neo4j 그래프 질문 유형을 특정하지 못함",
    }


# 3. Tool: graph status
# This is the first tool to call after Neo4j indexing.
@mcp.tool()
def check_graph_status() -> str:
    """
    Neo4j에 적재된 한자 그래프의 주요 노드/관계 수를 확인합니다.

    호출 조건:
      - Neo4j 적재 직후 검증
      - graph_db_node 연결 전 서버 상태 확인

    Returns:
        Hanja, Sound, Stroke, Category, Law 노드와 주요 관계 count.
    """
    query = """
    MATCH (h:Hanja {dataset: $dataset})
    WITH count(h) AS hanja_count
    MATCH (sound:Sound {dataset: $dataset})
    WITH hanja_count, count(sound) AS sound_count
    MATCH (stroke:Stroke {dataset: $dataset})
    WITH hanja_count, sound_count, count(stroke) AS stroke_count
    MATCH (cat:Category:Ohaeng {dataset: $dataset})
    WITH hanja_count, sound_count, stroke_count, count(cat) AS category_count
    MATCH (law:Law {dataset: $dataset})
    RETURN hanja_count, sound_count, stroke_count, category_count, count(law) AS law_count
    """
    relationship_query = """
    UNWIND $relationship_names AS rel_name
    CALL {
      WITH rel_name
      MATCH (:Hanja {dataset: $dataset})-[r]->()
      WHERE type(r) = rel_name
      RETURN count(r) AS count
    }
    RETURN rel_name, count
    """
    category_relationship_query = """
    UNWIND $relationship_names AS rel_name
    CALL {
      WITH rel_name
      MATCH (:Category:Ohaeng {dataset: $dataset})-[r]->(:Category:Ohaeng {dataset: $dataset})
      WHERE type(r) = rel_name
      RETURN count(r) AS count
    }
    RETURN rel_name, count
    """

    try:
        counts = _run_read(query, {"dataset": DATASET})[0]
        hanja_relationships = _run_read(
            relationship_query,
            {
                "dataset": DATASET,
                "relationship_names": ["HAS_SOUND", "HAS_STROKES", "BELONGS_TO", "PERMITTED_BY"],
            },
        )
        category_relationships = _run_read(
            category_relationship_query,
            {
                "dataset": DATASET,
                "relationship_names": ["GENERATES", "CONTROLS"],
            },
        )
    except Exception as error:
        return _format_neo4j_error(error)

    relationship_counts = {row["rel_name"]: row["count"] for row in hanja_relationships + category_relationships}

    lines = [
        "[Neo4j 그래프 적재 현황]",
        f"dataset: {DATASET}",
        "",
        f"  - Hanja: {counts['hanja_count']}건",
        f"  - Sound: {counts['sound_count']}건",
        f"  - Stroke: {counts['stroke_count']}건",
        f"  - Category:Ohaeng: {counts['category_count']}건",
        f"  - Law: {counts['law_count']}건",
        "",
        "관계:",
    ]

    for name in ["HAS_SOUND", "HAS_STROKES", "BELONGS_TO", "PERMITTED_BY", "GENERATES", "CONTROLS"]:
        actual = relationship_counts.get(name, 0)
        expected = EXPECTED_RELATIONSHIPS[name]
        status = "OK" if actual == expected else "확인 필요"
        lines.append(f"  - {name}: {actual}건 / 기대 {expected}건 / {status}")

    return "\n".join(lines)


# 4. Tool: Hanja lookup and permission check
# These tools support the graph_db route scenarios in docs/프로젝트_아이디어_작명.md.
@mcp.tool()
def lookup_hanja(
    hanja: str | None = None,
    hangul: str | None = None,
    profile_id: str | None = None,
    limit: int = 10,
) -> str:
    """
    한자, 한글 음, profile_id 중 하나 이상으로 Hanja 노드를 조회합니다.

    Args:
        hanja: 한자 문자. 예: "加"
        hangul: 한글 음. 예: "가"
        profile_id: 프로필 ID. 예: "OHE-00001"
        limit: 최대 반환 건수. 기본 10, 최대 30.

    Returns:
        한자 속성 목록.
    """
    limit = _safe_limit(limit)
    query = """
    MATCH (h:Hanja {dataset: $dataset})
    WHERE ($hanja IS NULL OR h.hanja = $hanja)
      AND ($hangul IS NULL OR h.hangul = $hangul)
      AND ($profile_id IS NULL OR h.profile_id = $profile_id)
    RETURN h.profile_id AS profile_id,
           h.hanja AS hanja,
           h.hangul AS hangul,
           h.sound_meaning AS sound_meaning,
           h.strokes AS strokes,
           h.sound_ohaeng AS sound_ohaeng,
           h.resource_ohaeng AS resource_ohaeng
    ORDER BY h.profile_id
    LIMIT $limit
    """
    params = {
        "dataset": DATASET,
        "hanja": hanja.strip() if isinstance(hanja, str) and hanja.strip() else None,
        "hangul": hangul.strip() if isinstance(hangul, str) and hangul.strip() else None,
        "profile_id": profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else None,
        "limit": limit,
    }
    if not any(params[key] for key in ["hanja", "hangul", "profile_id"]):
        return "[오류] hanja, hangul, profile_id 중 하나 이상을 입력하세요."

    try:
        rows = _run_read(query, params)
    except Exception as error:
        return _format_neo4j_error(error)

    return "[한자 조회 결과]\n" + _format_hanja_rows(rows)


@mcp.tool()
def check_person_name_hanja(hanja: str) -> str:
    """
    특정 한자가 인명용 한자로 허용되는지 Neo4j PERMITTED_BY 관계로 확인합니다.

    Args:
        hanja: 확인할 한자 문자. 예: "加"

    Returns:
        인명용 허용 여부와 근거 Law 노드.
    """
    if not hanja or not str(hanja).strip():
        return "[오류] 확인할 한자를 입력하세요."

    query = """
    MATCH (h:Hanja {dataset: $dataset, hanja: $hanja})
    OPTIONAL MATCH (h)-[:PERMITTED_BY]->(law:Law {dataset: $dataset})
    RETURN h.profile_id AS profile_id,
           h.hanja AS hanja,
           h.hangul AS hangul,
           h.sound_meaning AS sound_meaning,
           h.strokes AS strokes,
           h.sound_ohaeng AS sound_ohaeng,
           h.resource_ohaeng AS resource_ohaeng,
           law.name AS law_name,
           law.law_id AS law_id
    ORDER BY h.profile_id
    LIMIT 20
    """

    try:
        rows = _run_read(query, {"dataset": DATASET, "hanja": str(hanja).strip()})
    except Exception as error:
        return _format_neo4j_error(error)

    if not rows:
        return f"[결과 없음] '{hanja}' 한자를 Neo4j Hanja 그래프에서 찾지 못했습니다."

    lines = [f"[인명용 한자 허용 여부] {hanja}"]
    for row in rows:
        permitted = bool(row.get("law_id"))
        status = "허용 가능" if permitted else "허용 근거 없음"
        law_text = row.get("law_name") or "-"
        lines.append(
            f"  - {row['hanja']}({row['hangul']}) {row['sound_meaning']} / "
            f"{row['strokes']}획 / {status} / 근거: {law_text} / profile_id {row['profile_id']}"
        )
    lines.append("\n면책: 실제 출생신고 가능 여부를 100% 보장하지 않으며 최종 확인은 관할 기관 기준을 따르세요.")
    return "\n".join(lines)


# 5. Tool: Ohaeng graph traversal
# These tools support 상생/상극 questions in graph_db_node.
@mcp.tool()
def get_ohaeng_relations(element: str) -> str:
    """
    오행 하나의 상생/상극 관계를 Neo4j Category 그래프에서 조회합니다.

    Args:
        element: 목/화/토/금/수 또는 木/火/土/金/水

    Returns:
        해당 오행이 생성하는 상생 관계와 제어하는 상극 관계.
    """
    normalized = _normalize_ohaeng(element)
    if normalized is None:
        return "[오류] element는 목/화/토/금/수 또는 木/火/土/金/水 중 하나여야 합니다."

    query = """
    MATCH (c:Category:Ohaeng {dataset: $dataset, name: $name})
    OPTIONAL MATCH (c)-[:GENERATES]->(generated:Category:Ohaeng {dataset: $dataset})
    OPTIONAL MATCH (c)-[:CONTROLS]->(controlled:Category:Ohaeng {dataset: $dataset})
    RETURN c.name AS source,
           collect(DISTINCT generated.name) AS generates,
           collect(DISTINCT controlled.name) AS controls
    """

    try:
        rows = _run_read(query, {"dataset": DATASET, "name": normalized})
    except Exception as error:
        return _format_neo4j_error(error)

    if not rows:
        return f"[결과 없음] '{element}' 오행 카테고리를 찾지 못했습니다."

    row = rows[0]
    source_hanja = OHAENG_KO_TO_HANJA.get(row["source"], row["source"])
    generates = ", ".join(f"{name}({OHAENG_KO_TO_HANJA.get(name, name)})" for name in row["generates"]) or "-"
    controls = ", ".join(f"{name}({OHAENG_KO_TO_HANJA.get(name, name)})" for name in row["controls"]) or "-"

    return (
        f"[오행 관계 조회]\n"
        f"기준: {row['source']}({source_hanja})\n"
        f"상생(GENERATES): {generates}\n"
        f"상극(CONTROLS): {controls}"
    )


def get_hanja_by_sound_and_ohaeng(hangul: str, resource_ohaeng: str, limit: int = 5) -> list[dict]:
    """발음 + 자원오행으로 인명용 한자를 Neo4j에서 조회합니다 (PERMITTED_BY 검증됨).
    MCP tool이 아닌 내부 호출 전용 함수입니다."""
    query = """
    MATCH (h:Hanja {dataset: $dataset, hangul: $hangul, resource_ohaeng: $ohaeng})
          -[:PERMITTED_BY]->(:Law {dataset: $dataset})
    RETURN h.hanja AS hanja,
           h.hangul AS hangul,
           h.sound_meaning AS sound_meaning,
           h.strokes AS strokes,
           h.resource_ohaeng AS resource_ohaeng,
           h.sound_ohaeng AS sound_ohaeng
    ORDER BY h.profile_id
    LIMIT $limit
    """
    try:
        return _run_read(query, {
            "dataset": DATASET,
            "hangul": hangul.strip(),
            "ohaeng": resource_ohaeng.strip(),
            "limit": _safe_limit(limit, default=5, maximum=20),
        })
    except Exception:
        return []


def get_ohaeng_pairs() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Neo4j GENERATES/CONTROLS 엣지에서 상생/상극 쌍을 로드합니다.
    반환: (sangsaeng_set, sanggeuk_set) 각각 (a, b) 쌍 집합."""
    sangsaeng: set[tuple[str, str]] = set()
    sanggeuk: set[tuple[str, str]] = set()
    query = """
    MATCH (a:Category:Ohaeng {dataset: $dataset})-[r]->(b:Category:Ohaeng {dataset: $dataset})
    WHERE type(r) IN ['GENERATES', 'CONTROLS']
    RETURN a.name AS src, type(r) AS rel, b.name AS dst
    """
    try:
        rows = _run_read(query, {"dataset": DATASET})
        for row in rows:
            src, rel, dst = row["src"], row["rel"], row["dst"]
            if rel == "GENERATES":
                sangsaeng.add((src, dst))
            elif rel == "CONTROLS":
                sanggeuk.add((src, dst))
    except Exception:
        pass
    return sangsaeng, sanggeuk


@mcp.tool()
def recommend_hanja_by_ohaeng(
    sound_ohaeng: str | None = None,
    resource_ohaeng: str | None = None,
    hangul: str | None = None,
    meaning_keyword: str | None = None,
    strokes: int | None = None,
    limit: int = 10,
) -> str:
    """
    오행, 음, 뜻 키워드, 획수 조건으로 인명용 한자를 필터링합니다.

    Args:
        sound_ohaeng: 발음오행. 목/화/토/금/수 또는 木/火/土/金/水
        resource_ohaeng: 자원오행. 목/화/토/금/수 또는 木/火/土/金/水
        hangul: 한글 음. 예: "서"
        meaning_keyword: 뜻 키워드. 예: "밝", "지혜"
        strokes: 원획법 획수
        limit: 최대 반환 건수. 기본 10, 최대 30.

    Returns:
        조건을 만족하는 한자 목록.
    """
    sound_ohaeng = _normalize_ohaeng(sound_ohaeng)
    resource_ohaeng = _normalize_ohaeng(resource_ohaeng)
    hangul = hangul.strip() if isinstance(hangul, str) and hangul.strip() else None
    meaning_keyword = meaning_keyword.strip() if isinstance(meaning_keyword, str) and meaning_keyword.strip() else None
    limit = _safe_limit(limit)

    try:
        strokes_value = int(strokes) if strokes is not None else None
    except (TypeError, ValueError):
        return "[오류] strokes는 정수여야 합니다."

    if not any([sound_ohaeng, resource_ohaeng, hangul, meaning_keyword, strokes_value]):
        return "[오류] 발음오행, 자원오행, 음, 뜻 키워드, 획수 중 하나 이상의 조건을 입력하세요."

    query = """
    MATCH (h:Hanja {dataset: $dataset})-[:PERMITTED_BY]->(:Law {dataset: $dataset})
    WHERE ($sound_ohaeng IS NULL OR h.sound_ohaeng = $sound_ohaeng)
      AND ($resource_ohaeng IS NULL OR h.resource_ohaeng = $resource_ohaeng)
      AND ($hangul IS NULL OR h.hangul = $hangul)
      AND ($meaning_keyword IS NULL OR h.sound_meaning CONTAINS $meaning_keyword)
      AND ($strokes IS NULL OR h.strokes = $strokes)
    RETURN h.profile_id AS profile_id,
           h.hanja AS hanja,
           h.hangul AS hangul,
           h.sound_meaning AS sound_meaning,
           h.strokes AS strokes,
           h.sound_ohaeng AS sound_ohaeng,
           h.resource_ohaeng AS resource_ohaeng
    ORDER BY h.profile_id
    LIMIT $limit
    """
    params = {
        "dataset": DATASET,
        "sound_ohaeng": sound_ohaeng,
        "resource_ohaeng": resource_ohaeng,
        "hangul": hangul,
        "meaning_keyword": meaning_keyword,
        "strokes": strokes_value,
        "limit": limit,
    }

    try:
        rows = _run_read(query, params)
    except Exception as error:
        return _format_neo4j_error(error)

    conditions = []
    if sound_ohaeng:
        conditions.append(f"발음오행={sound_ohaeng}")
    if resource_ohaeng:
        conditions.append(f"자원오행={resource_ohaeng}")
    if hangul:
        conditions.append(f"음={hangul}")
    if meaning_keyword:
        conditions.append(f"뜻 포함={meaning_keyword}")
    if strokes_value:
        conditions.append(f"획수={strokes_value}")

    return f"[조건 기반 한자 추천] {' / '.join(conditions)}\n" + _format_hanja_rows(rows)


@mcp.tool()
def answer_graph_query(query: str, limit: int = 10) -> str:
    """
    자연어 그래프 질문을 받아 적절한 Neo4j 조회 Tool로 분기합니다.

    이 함수는 LangGraph의 graph_db_node나 Web AI Chat 서버가 graph_server.py의
    세부 Tool 이름을 몰라도 하나의 진입점으로 사용할 수 있게 만든 얇은 라우터입니다.

    Args:
        query: 사용자의 자연어 질문.
        limit: 추천/조회 최대 반환 건수. 기본 10, 최대 30.

    Returns:
        분기된 Neo4j 조회 결과 또는 추가 입력 안내.
    """
    query = str(query or "").strip()
    if not query:
        return "[오류] graph query가 비어 있습니다."

    limit = _safe_limit(limit)
    route = _classify_graph_query(query, limit=limit)
    tool = route["tool"]
    params = route["params"]
    header = f"[graph_server 라우팅]\n선택 Tool: {tool}\n이유: {route['reason']}\n\n"

    if tool == "check_graph_status":
        return header + check_graph_status()
    if tool == "lookup_hanja":
        return header + lookup_hanja(**params)
    if tool == "check_person_name_hanja":
        return header + check_person_name_hanja(**params)
    if tool == "get_ohaeng_relations":
        return header + get_ohaeng_relations(**params)
    if tool == "recommend_hanja_by_ohaeng":
        return header + recommend_hanja_by_ohaeng(**params)
    if tool == "needs_hanja_for_permission":
        return (
            header
            + "[안내] Neo4j 인명용 한자 검증은 한자 문자가 필요합니다.\n"
            + "예: '牧 한자는 인명용으로 허용되나요?'처럼 한자를 포함해 질문해주세요."
        )

    return (
        header
        + "[안내] graph_server가 처리할 수 있는 질문 유형은 한자 조회, 인명용 한자 검증, "
        + "오행 상생/상극 조회, 오행/뜻/획수 조건 기반 한자 추천입니다."
    )


# 6. Local self-check without Neo4j connection
# This is used before touching the actual server.
def _read_source_records() -> list[dict[str, Any]]:
    with SOURCE_PATH.open("r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise ValueError("hanja_documents.json must be a list")
    return records


def _build_expected_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    metadatas = [item.get("metadata", {}) for item in records if isinstance(item, dict)]
    return {
        "Hanja": len(records),
        "Sound": len({meta.get("hangul") for meta in metadatas}),
        "Stroke": len({meta.get("strokes") for meta in metadatas}),
        "Category:Ohaeng": len(OHAENG_KO),
        "Law": 1 if records else 0,
        **EXPECTED_RELATIONSHIPS,
    }


def run_self_check() -> int:
    _configure_stdout()
    issues: list[str] = []

    if not SOURCE_PATH.exists():
        issues.append(f"missing source file: {SOURCE_PATH}")
        records: list[dict[str, Any]] = []
    else:
        records = _read_source_records()

    if records:
        required_fields = {
            "profile_id",
            "hangul",
            "hanja",
            "unicode",
            "sound_meaning",
            "strokes",
            "sound_ohaeng",
            "resource_ohaeng",
            "is_person_name_hanja",
        }
        for index, item in enumerate(records[:], 1):
            if not isinstance(item, dict):
                issues.append(f"record {index}: item is not object")
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                issues.append(f"record {index}: metadata is not object")
                continue
            missing = required_fields - set(metadata)
            if missing:
                issues.append(f"record {index}: missing metadata fields {sorted(missing)}")
            if metadata.get("sound_ohaeng") not in OHAENG_KO:
                issues.append(f"record {index}: invalid sound_ohaeng {metadata.get('sound_ohaeng')}")
            if metadata.get("resource_ohaeng") not in OHAENG_KO:
                issues.append(f"record {index}: invalid resource_ohaeng {metadata.get('resource_ohaeng')}")

    source_text = Path(__file__).read_text(encoding="utf-8")
    source_tree = ast.parse(source_text, filename=__file__)
    module_level_driver_call = any(
        isinstance(node, (ast.Assign, ast.Expr))
        and "GraphDatabase.driver" in ast.unparse(node)
        for node in source_tree.body
    )

    checks = {
        "driver_connection_is_lazy": not module_level_driver_call,
        "has_status_tool": "def check_graph_status" in Path(__file__).read_text(encoding="utf-8"),
        "has_lookup_tool": "def lookup_hanja" in source_text,
        "has_permission_tool": "def check_person_name_hanja" in source_text,
        "has_ohaeng_tool": "def get_ohaeng_relations" in source_text,
        "has_recommend_tool": "def recommend_hanja_by_ohaeng" in source_text,
        "has_answer_graph_query_tool": "def answer_graph_query" in source_text,
    }
    for name, ok in checks.items():
        if not ok:
            issues.append(f"self-check failed: {name}")

    route_samples = [
        ("Neo4j 상태 확인해줘", "check_graph_status"),
        ("OHE-00730 한자 조회해줘", "lookup_hanja"),
        ("牧 한자는 인명용으로 허용되나요?", "check_person_name_hanja"),
        ("목 오행 상생 상극 관계 알려줘", "get_ohaeng_relations"),
        ("목 오행이고 밝은 뜻 한자 추천해줘", "recommend_hanja_by_ohaeng"),
    ]
    route_results = []
    for sample, expected_tool in route_samples:
        actual_tool = _classify_graph_query(sample)["tool"]
        ok = actual_tool == expected_tool
        route_results.append((sample, expected_tool, actual_tool, ok))
        if not ok:
            issues.append(f"route sample failed: {sample} expected={expected_tool} actual={actual_tool}")

    counts = _build_expected_counts(records) if records else {}
    print("[graph_server.py self-check]")
    print(f"source: {SOURCE_PATH}")
    print(f"source_records: {len(records)}")
    if counts:
        print("expected_counts:")
        for key, value in counts.items():
            print(f"  - {key}: {value}")
    print("tool_structure:")
    for name, ok in checks.items():
        print(f"  - {name}: {'OK' if ok else 'FAILED'}")
    print("route_samples:")
    for sample, expected_tool, actual_tool, ok in route_results:
        print(f"  - {sample} -> {actual_tool}: {'OK' if ok else f'FAILED expected {expected_tool}'}")

    if issues:
        print("\nissues:")
        for issue in issues[:50]:
            print(f"  - {issue}")
        if len(issues) > 50:
            print(f"  - ... {len(issues) - 50} more issue(s)")
        return 1

    print("\nresult: OK")
    print("No Neo4j connection was opened during this self-check.")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Neo4j graph MCP server for naming QA.")
    parser.add_argument("--self-check", action="store_true", help="Validate local data and tool structure without Neo4j connection.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.self_check:
        raise SystemExit(run_self_check())
    mcp.run()
