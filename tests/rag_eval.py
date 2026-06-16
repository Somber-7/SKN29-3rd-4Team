# -*- coding: utf-8 -*-
"""
rag_eval.py — LLM-as-a-Judge RAG 평가 스크립트

Judge 모델: gpt-5.4 (full)
평가 지표:
  1. Context Relevance  — 검색된 컨텍스트가 질문과 얼마나 관련 있는가
  2. Groundedness       — 답변이 컨텍스트에 근거하는가 (환각 여부)
  3. Answer Relevance   — 답변이 질문에 실제로 답하는가

사용법:
  python tests/rag_eval.py              # 전체 케이스 평가
  python tests/rag_eval.py --case 3    # 특정 케이스만
  python tests/rag_eval.py --output eval_result.json  # 결과 JSON 저장
"""
import sys
import os
import json
import re
import time
import argparse

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "mcp"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "graph"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

# ─────────────────────────────────────────────
# Judge LLM (gpt-5.4 full — pipeline 모델(gpt-5.4-mini)보다 강한 judge)
# ─────────────────────────────────────────────
_judge = ChatOpenAI(model="gpt-5.4", temperature=0, max_tokens=512)

# ─────────────────────────────────────────────
# 평가 대상 테스트 케이스
# ─────────────────────────────────────────────
EVAL_CASES = [
    (1,  "한자 수리 복합",      "김씨(8획) 성에 어울리는 吉수 획수 조합과 밝은 뜻의 木오행 한자를 추천해줘"),
    (2,  "오행 상생 설명",      "木火土 오행 조합이 상생인지 알려줘"),
    (3,  "순우리말 이름",       "밝고 아름다운 뜻의 순우리말 이름 3개 추천해줘"),
    (4,  "법령 조문",           "인명용 한자에 관한 법령을 찾아줘"),
    (5,  "논문/트렌드",         "최근 한국 이름 트렌드와 음절 특성 연구 결과를 알려줘"),
    (6,  "그래프 DB 탐색",      "목 오행 상생 상극 관계를 Neo4j 그래프로 알려줘"),
    (7,  "이름 수리 계산",      "성씨 획수 8, 이름 첫째 7획 둘째 6획일 때 81수리 4격을 계산해줘"),
    (8,  "통합 추천",           "최씨 성에 火오행이고 吉수이며 지혜로운 뜻의 한자 이름을 추천해줘"),
    (9,  "순우리말 외자",       "순우리말 외자 이름 3개 추천해줘"),
    (10, "순우리말 외자+성씨",  "임씨 성에 어울리는 순우리말 외자 이름 2개 추천해줘"),
    (11, "순우리말 성씨 포함",  "박씨 성을 가진 여자아이 순우리말 이름 2개 추천해줘"),
]

# ─────────────────────────────────────────────
# Judge 프롬프트
# ─────────────────────────────────────────────
_JUDGE_SYSTEM = """당신은 RAG(검색 증강 생성) 시스템의 품질을 평가하는 전문 평가자입니다.
아래 세 가지 지표를 각각 1~5점으로 채점하고, 반드시 JSON 형식으로만 출력하세요.

[평가 지표]
1. context_relevance (맥락 관련성)
   - 검색된 컨텍스트가 사용자 질문에 답하기 위해 필요한 정보를 포함하는가
   - 1: 전혀 관련 없음 / 3: 부분적으로 관련 / 5: 질문에 정확히 필요한 정보 포함

2. groundedness (근거성 / 환각 여부)
   - 생성된 답변이 제공된 컨텍스트에 근거하는가 (컨텍스트 외 정보를 지어내지 않는가)
   - 1: 컨텍스트 무시, 대부분 환각 / 3: 일부 근거, 일부 추론 / 5: 컨텍스트에 완전히 근거

3. answer_relevance (답변 관련성)
   - 생성된 답변이 사용자 질문에 실질적으로 답하는가
   - 1: 질문과 무관한 답변 / 3: 부분적으로 답변 / 5: 질문에 완전하고 명확하게 답변

출력 형식 (JSON만, 다른 텍스트 절대 금지):
{
  "context_relevance": <1-5>,
  "groundedness": <1-5>,
  "answer_relevance": <1-5>,
  "reason": "<3가지 지표에 대한 간략한 근거 1~2문장>"
}"""


def _judge_single(query: str, context: str, answer: str) -> dict:
    """단일 케이스에 대해 judge LLM으로 3가지 지표를 평가합니다."""
    content = (
        f"[사용자 질문]\n{query}\n\n"
        f"[검색된 컨텍스트]\n{context}\n\n"
        f"[생성된 답변]\n{answer}"
    )
    try:
        resp = _judge.invoke([
            SystemMessage(content=_JUDGE_SYSTEM),
            HumanMessage(content=content),
        ])
        raw = resp.content.strip()
        raw = re.sub(r"```json\s*|\s*```", "", raw).strip()
        parsed = json.loads(raw)
        return {
            "context_relevance": int(parsed.get("context_relevance", 0)),
            "groundedness":      int(parsed.get("groundedness", 0)),
            "answer_relevance":  int(parsed.get("answer_relevance", 0)),
            "reason":            parsed.get("reason", ""),
        }
    except Exception as e:
        return {"context_relevance": 0, "groundedness": 0, "answer_relevance": 0, "reason": f"[평가 오류: {e}]"}


# ─────────────────────────────────────────────
# 파이프라인 실행 (context 포함 반환)
# ─────────────────────────────────────────────
def run_pipeline(query: str, app) -> dict:
    """파이프라인을 실행하고 state 전체(context + answer 포함)를 반환합니다."""
    init_state = {
        "query":       query,
        "context":     "",
        "next_action": "generate",
        "answer":      "",
        "iterations":  0,
        "used_tools":  [],
        "collections": [],
        "name_length": 2,
        "surname_hanja": "",
    }
    return app.invoke(init_state)


# ─────────────────────────────────────────────
# 평가 실행
# ─────────────────────────────────────────────
def run_eval(case_num: int | None, app) -> list[dict]:
    cases = EVAL_CASES if case_num is None else [c for c in EVAL_CASES if c[0] == case_num]
    if not cases:
        print(f"[오류] 케이스 {case_num}번이 없습니다.")
        return []

    results = []
    sep = "─" * 60

    print(f"\n{'='*60}")
    print(f" RAG LLM-as-a-Judge 평가  (Judge: gpt-5.4)")
    print(f" 평가 케이스: {len(cases)}개")
    print(f"{'='*60}")

    for num, desc, query in cases:
        print(f"\n▶ [{num}] {desc}")
        print(f"   질문: {query}")

        # 파이프라인 실행
        t0 = time.time()
        state = run_pipeline(query, app)
        elapsed = time.time() - t0

        answer  = state.get("answer", "").strip()
        context = state.get("context", "").strip()
        tools   = state.get("used_tools", [])

        print(f"   도구: {tools} | {elapsed:.1f}s")

        if not answer or answer.startswith("[오류]"):
            print(f"   [SKIP] 답변 생성 실패")
            results.append({
                "case": num, "desc": desc, "query": query,
                "answer": answer, "context": context, "tools": tools,
                "context_relevance": 0, "groundedness": 0, "answer_relevance": 0,
                "reason": "답변 생성 실패", "elapsed": elapsed,
            })
            continue

        # Judge 평가
        scores = _judge_single(query, context, answer)
        print(f"   {sep}")
        print(f"   Context Relevance : {scores['context_relevance']}/5")
        print(f"   Groundedness      : {scores['groundedness']}/5")
        print(f"   Answer Relevance  : {scores['answer_relevance']}/5")
        print(f"   근거: {scores['reason']}")

        results.append({
            "case": num, "desc": desc, "query": query,
            "answer": answer, "context": context, "tools": tools,
            "elapsed": elapsed,
            **scores,
        })

    return results


def print_summary(results: list[dict]):
    """평가 결과 요약을 출력합니다."""
    if not results:
        return

    valid = [r for r in results if r["context_relevance"] > 0]
    if not valid:
        print("\n[경고] 유효한 평가 결과가 없습니다.")
        return

    avg_cr = sum(r["context_relevance"] for r in valid) / len(valid)
    avg_gr = sum(r["groundedness"]      for r in valid) / len(valid)
    avg_ar = sum(r["answer_relevance"]  for r in valid) / len(valid)
    avg_total = (avg_cr + avg_gr + avg_ar) / 3

    print(f"\n{'='*60}")
    print(f" 평가 결과 요약  (유효 케이스: {len(valid)}/{len(results)}개)")
    print(f"{'='*60}")
    print(f" {'지표':<22} {'평균':>6}  {'분포 (케이스별)'}")
    print(f" {'─'*56}")

    for label, key in [
        ("Context Relevance", "context_relevance"),
        ("Groundedness",      "groundedness"),
        ("Answer Relevance",  "answer_relevance"),
    ]:
        avg = sum(r[key] for r in valid) / len(valid)
        dist = " ".join(str(r[key]) for r in results)
        print(f" {label:<22} {avg:>5.2f}  [{dist}]")

    print(f" {'─'*56}")
    print(f" {'종합 평균':<22} {avg_total:>5.2f}")
    print(f"{'='*60}\n")

    # 케이스별 상세
    print(f" {'#':<3} {'설명':<16} {'CR':>3} {'GR':>3} {'AR':>3} {'평균':>5}")
    print(f" {'─'*44}")
    for r in results:
        cr = r["context_relevance"]
        gr = r["groundedness"]
        ar = r["answer_relevance"]
        avg = (cr + gr + ar) / 3 if cr > 0 else 0
        flag = " ⚠️" if avg < 3 and cr > 0 else ""
        print(f" {r['case']:<3} {r['desc']:<16} {cr:>3} {gr:>3} {ar:>3} {avg:>5.2f}{flag}")
    print()


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG LLM-as-a-Judge 평가")
    parser.add_argument("--case",   "-c", type=int, default=None, help="평가할 케이스 번호 (미지정 시 전체)")
    parser.add_argument("--output", "-o", type=str, default=None, help="결과 저장 경로 (JSON)")
    args = parser.parse_args()

    print("그래프 빌드 중...")
    from naming_graph import build_graph
    app = build_graph()
    print("준비 완료.\n")

    results = run_eval(args.case, app)
    print_summary(results)

    if args.output and results:
        out_path = args.output
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        save_data = [
            {k: v for k, v in r.items() if k not in ("context",)}
            for r in results
        ]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"결과 저장: {out_path}")
