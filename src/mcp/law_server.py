"""
law_server.py — 외부 API 연동 MCP 서버 (external_api 노드)

내부 벡터DB(ChromaDB)에 수록되지 않은 법령 조문을 실시간으로 조회하거나,
LLM이 추천한 순우리말 이름 후보가 실제 존재하는 단어인지 검증하는 역할.

[Tool 목록]
  1. search_law         — 국가법령정보 목록 조회 (lawSearch.do)
  2. get_law_article    — 국가법령정보 본문 조회 (lawService.do)
  3. verify_korean_word — 우리말샘 API 순우리말 검증
"""

import os
import time
import requests
import re
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

# ─────────────────────────────────────────────
# FastMCP 서버 인스턴스
# ─────────────────────────────────────────────
mcp = FastMCP("LawServer")


# ─────────────────────────────────────────────
# 내부 유틸: 재시도 + 지수적 백오프
# ─────────────────────────────────────────────
def _request_with_retry(
    url: str,
    params: dict = None,
    max_retries: int = 3,
    timeout: int = 5,
) -> dict:
    """
    공공 API 특성상 응답 지연·일시 장애가 빈번하므로
    지수적 백오프(Exponential Backoff) 재시도 로직을 적용합니다.
    """
    for attempt in range(max_retries):
        try:
            res = requests.get(url, params=params, timeout=timeout)
            res.raise_for_status()
            return res.json()
        except (requests.exceptions.Timeout, requests.exceptions.RequestException, ValueError):
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)


def _ensure_list(data) -> list:
    """정부 API가 단일 결과를 dict로 반환하는 이슈 대응."""
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    return []


# ═══════════════════════════════════════════════════════
# Tool 1: 국가법령정보 — 법령 목록 검색 (lawSearch.do)
# ═══════════════════════════════════════════════════════
@mcp.tool()
def search_law(query: str) -> str:
    """
    키워드로 법령을 검색하여 관련 법령 목록을 반환합니다.
    반환되는 법령ID(ls_id)는 get_law_article 도구에 전달하여 조문 내용을 조회할 수 있습니다.

    호출 조건:
      - 내부 벡터DB(ChromaDB)에서 관련 법령을 찾지 못했을 때 호출
      - 사용자가 법적 근거를 요청했으나 내부 문서로 답변이 불충분할 때 호출

    호출하지 말아야 할 때:
      - 내부 DB에 이미 해당 법령 정보가 있을 때
      - 개별 한자의 인명용 허용 여부 확인 시 (peoplehanja.json / Neo4j 우선)

    Args:
        query: 법령명 또는 검색 키워드 (예: "가족관계", "인명용 한자", "출생신고")

    Returns:
        검색된 법령 목록 (법령명, 법령ID, 시행일 포함)
    """
    api_key = os.getenv("LAW_API_KEY")
    if not api_key:
        return "[오류] LAW_API_KEY 환경변수가 설정되지 않았습니다."

    try:
        search_url = "https://www.law.go.kr/DRF/lawSearch.do"
        search_params = {
            "OC": api_key,
            "target": "law",
            "type": "JSON",
            "query": query,
        }
        data = _request_with_retry(search_url, params=search_params)

        ls = data.get("LawSearch") or {}
        total = ls.get("totalCnt", "0")
        law_list = _ensure_list(ls.get("law", []))

        if not law_list:
            return f"[검색 결과 없음] '{query}'에 해당하는 법령을 찾을 수 없습니다. 다른 키워드로 검색해보세요."

        # 상위 10건만 반환 (너무 많으면 LLM 컨텍스트 낭비)
        results = []
        for idx, law in enumerate(law_list[:10], 1):
            law_name = law.get("법령명한글", "이름 없음")
            mst = law.get("법령일련번호", "")
            ef_yd = law.get("시행일자", "정보 없음")

            results.append(
                f"  {idx}. {law_name}\n"
                f"     법령일련번호(MST): {mst}\n"
                f"     시행일: {ef_yd}"
            )

        header = f"[검색 결과] '{query}' 관련 법령 {len(results)}건 (전체 {total}건)\n"
        body = "\n".join(results)
        footer = "\n\n조문 내용을 확인하려면 get_law_article 도구에 법령일련번호(MST)를 전달하세요."

        return header + "\n" + body + footer

    except requests.exceptions.Timeout:
        return "[오류] 국가법령정보센터 서버 응답 지연. 잠시 후 다시 시도하세요."
    except Exception as e:
        return f"[오류] 법령 검색 중 오류 발생: {str(e)}"


# ═══════════════════════════════════════════════════════
# Tool 2: 국가법령정보 — 법령 본문 조회 (lawService.do)
# ═══════════════════════════════════════════════════════
@mcp.tool()
def get_law_article(mst: str, article_num: str | None = None) -> str:
    """
    search_law에서 확인한 법령일련번호(MST)로 법령 본문을 조회합니다.

    사용 방법:
      - article_num 미입력: 해당 법령의 전체 조문 번호 목록을 반환합니다.
      - article_num 입력: 해당 조문의 본문 텍스트를 반환합니다.

    일반적인 호출 흐름:
      1) search_law("가족관계")로 법령 목록과 법령일련번호(MST) 확인
      2) get_law_article(mst="...", article_num=None)으로 조문 목록 확인
      3) get_law_article(mst="...", article_num="44")로 필요한 조문 본문 조회

    주의:
      - 이 API는 법령 '조문 텍스트'만 반환합니다.
      - 인명용 한자 9,389자 전체 목록은 제공하지 않습니다.

    Args:
        mst: search_law로 확인한 법령일련번호 (예: "257203")
        article_num: 조문 번호 (예: "44"). 미입력 시 조문 목록 반환.

    Returns:
        조문 목록 또는 특정 조문의 본문 텍스트
    """
    api_key = os.getenv("LAW_API_KEY")
    if not api_key:
        return "[오류] LAW_API_KEY 환경변수가 설정되지 않았습니다."

    try:
        body_url = "https://www.law.go.kr/DRF/lawService.do"
        body_params = {
            "OC": api_key,
            "target": "law",
            "type": "JSON",
            "MST": mst,
        }
        law_data = _request_with_retry(body_url, params=body_params)

        law_body = law_data.get("법령") or {}
        law_name = (law_body.get("기본정보") or {}).get("법령명_한글", "법령명 미상")
        jo_list = _ensure_list((law_body.get("조문") or {}).get("조문단위", []))

        if not jo_list:
            return f"[결과 없음] 법령일련번호 '{mst}'에 해당하는 조문을 찾을 수 없습니다."

        # ── article_num이 없으면: 조문 목록 반환 ──
        if article_num is None:
            article_entries = []
            for jo in jo_list:
                jo_no = jo.get("조문번호", "?")
                jo_content = jo.get("조문내용", "")
                # 조문내용 첫 줄만 제목으로 사용
                jo_title = jo_content.strip().splitlines()[0][:30] if jo_content.strip() else ""
                label = f"  - 제{jo_no}조"
                if jo_title:
                    label += f" {jo_title}"
                article_entries.append(label)

            header = f"[조문 목록] {law_name} (총 {len(article_entries)}개 조문)\n"
            body = "\n".join(article_entries)
            footer = "\n\n특정 조문의 본문을 확인하려면 article_num에 조문 번호를 입력하세요."

            return header + "\n" + body + footer

        # ── article_num이 있으면: 해당 조문 본문 반환 ──
        # 숫자와 '의' 글자만 추출 (예: "제12조의2" -> "12의2")
        target_num = "".join(re.findall(r'[0-9의]', str(article_num)))

        for jo in jo_list:
            jo_no_raw = str(jo.get("조문번호", ""))
            jo_no_digits = "".join(re.findall(r'[0-9의]', jo_no_raw))

            if jo_no_digits == target_num:
                jo_content = jo.get("조문내용", "")

                header = f"[조문 내용] {law_name} 제{target_num}조"
                source = f"\n\n출처: 국가법령정보센터 — {law_name}"
                return header + "\n\n" + jo_content + source

        return f"[결과 없음] {law_name}에서 제{target_num}조를 찾을 수 없습니다."

    except requests.exceptions.Timeout:
        return "[오류] 국가법령정보센터 서버 응답 지연. 잠시 후 다시 시도하세요."
    except Exception as e:
        return f"[오류] 법령 본문 조회 중 오류 발생: {str(e)}"


# ═══════════════════════════════════════════════════════
# Tool 3: 우리말샘 API — 순우리말 검증
# ═══════════════════════════════════════════════════════
@mcp.tool()
def verify_korean_word(word: str) -> str:
    """
    LLM이 추천한 순우리말 이름 후보가 실제 존재하는 고유어 명사인지 검증합니다.

    호출 조건:
      - LLM이 순우리말 이름을 생성/추천한 경우, 각 후보에 대해 호출
      - 내부 DB에 해당 단어 정보가 없을 때 호출

    결과 해석:
      - "검증 성공" → 실제 순우리말 명사. 뜻풀이를 사용자 답변에 포함하세요.
      - "검증 실패" → 존재하지 않는 단어. 환각(Hallucination) 가능성. 추천 목록에서 제외하세요.
      - "API 오류"  → 검증 불가. 사용자에게 확인 불가 사유를 안내하세요.

    Args:
        word: 검증할 순우리말 이름 후보 (예: "가람", "나래", "다솜")

    Returns:
        검증 결과 (존재 여부 + 뜻풀이 또는 실패 사유)
    """
    api_key = os.getenv("URIMALSAM_API_KEY")
    if not api_key:
        return "[오류] URIMALSAM_API_KEY 환경변수가 설정되지 않았습니다."

    url = "https://opendict.korean.go.kr/api/search"
    params = {
        "key": api_key,
        "q": word,
        "req_type": "json",
        "method": "exact",
    }

    try:
        res = _request_with_retry(url, params=params)

        # ── 우리말샘 API 에러 코드 처리 ──
        error = res.get("error")
        if error:
            error_code = error.get("error_code", "알 수 없음")
            error_msg = error.get("message", "알 수 없는 오류")
            return (
                f"[API 오류] 우리말샘 에러 (코드: {error_code})\n"
                f"메시지: {error_msg}\n"
                f"검증 불가 — API 설정을 확인하세요."
            )

        channel = res.get("channel", {})
        total = int(channel.get("total", 0))

        # ── 검증 실패: 환각 의심 ──
        if total == 0:
            return (
                f"[결과: 검증 실패]\n"
                f"단어: {word}\n"
                f"사유: 우리말샘에 순우리말 명사로 등록되어 있지 않음. "
                f"환각 가능성이 높으므로 추천 목록에서 제외 필요."
            )

        # ── 검증 성공: 뜻풀이 추출 ──
        items = _ensure_list(channel.get("item", []))

        definitions = []
        for idx, item in enumerate(items, 1):
            word_text = item.get("word", word)
            sense = item.get("sense", {})

            # sense가 리스트 또는 딕셔너리일 수 있음
            sense_list = _ensure_list(sense) if isinstance(sense, (dict, list)) else []

            for s in sense_list:
                definition = s.get("definition", "뜻풀이 없음")
                link = s.get("link", "")
                entry = f"  {idx}. {word_text}: {definition}"
                if link:
                    entry += f"\n     출처URL: {link}"
                definitions.append(entry)

        header = f"[결과: 검증 성공]\n단어: {word}\n의미 수: {total}개\n"
        body = "\n".join(definitions)
        source = "\n\n출처: 국립국어원 우리말샘"

        return header + "\n" + body + source

    except requests.exceptions.Timeout:
        return "[오류] 우리말샘 서버 응답 지연. 뜻풀이 확인이 일시적으로 불가합니다."
    except Exception as e:
        return f"[오류] 우리말샘 조회 중 오류 발생: {str(e)}"


# ─────────────────────────────────────────────
# 서버 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
