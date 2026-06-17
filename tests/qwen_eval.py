# -*- coding: utf-8 -*-
"""
qwen_eval.py — Qwen LoRA 파인튜닝 모델 LLM-as-a-Judge 평가

평가 방식: Qwen API 직접 호출 (RAG/파이프라인 없음)
Judge 모델: gpt-5.4 (full)
평가 지표:
  1. Factual Accuracy   — 한자·오행·수리 정보가 사실에 부합하는가
  2. Groundedness       — 답변이 추론 없이 구체적 근거(한자 뜻·획수·오행)를 포함하는가
  3. Answer Relevance   — 답변이 사용자 질문에 실질적으로 답하는가

사용법:
  python tests/qwen_eval.py                          # 전체 케이스 평가
  python tests/qwen_eval.py --case 3                 # 특정 케이스만
  python tests/qwen_eval.py --output result.json     # 결과 JSON 저장
  python tests/qwen_eval.py --url https://...        # Qwen 서버 URL 지정
"""
import sys
import os
import json
import re
import time
import argparse

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

# ─────────────────────────────────────────────
# Judge LLM
# ─────────────────────────────────────────────
_judge = ChatOpenAI(model="gpt-5.4", temperature=0, max_tokens=512)

# ─────────────────────────────────────────────
# 평가 케이스 (rag_eval.py 와 동일한 11개)
# ─────────────────────────────────────────────
EVAL_CASES = [
    (1,  "한자 수리 복합",      "김씨(8획) 성에 어울리는 吉수 획수 조합과 밝은 뜻의 木오행 한자를 추천해줘"),
    (2,  "오행 상생 설명",      "木火土 오행 조합이 상생인지 알려줘"),
    (3,  "순우리말 이름",       "밝고 아름다운 뜻의 순우리말 이름 3개 추천해줘"),
    (4,  "법령 조문",           "인명용 한자에 관한 법령을 찾아줘"),
    (5,  "논문/트렌드",         "최근 한국 이름 트렌드와 음절 특성 연구 결과를 알려줘"),
    (6,  "그래프 DB 탐색",      "목 오행 상생 상극 관계를 알려줘"),
    (7,  "이름 수리 계산",      "성씨 획수 8, 이름 첫째 7획 둘째 6획일 때 81수리 4격을 계산해줘"),
    (8,  "통합 추천",           "최씨 성에 火오행이고 吉수이며 지혜로운 뜻의 한자 이름을 추천해줘"),
    (9,  "순우리말 외자",       "순우리말 외자 이름 3개 추천해줘"),
    (10, "순우리말 외자+성씨",  "임씨 성에 어울리는 순우리말 외자 이름 2개 추천해줘"),
    (11, "순우리말 성씨 포함",  "박씨 성을 가진 여자아이 순우리말 이름 2개 추천해줘"),
]

# ─────────────────────────────────────────────
# Judge 프롬프트 (RAG 컨텍스트 없는 직접 생성 평가)
# ─────────────────────────────────────────────
_JUDGE_SYSTEM = """당신은 한국 이름 추천 AI 모델의 출력 품질을 평가하는 전문 평가자입니다.
이 모델은 RAG(검색 증강 생성) 없이 파인튜닝된 지식만으로 답변을 생성합니다.
아래 세 가지 지표를 각각 1~5점으로 채점하고, 반드시 JSON 형식으로만 출력하세요.

[평가 지표]
1. factual_accuracy (사실 정확성)
   - 제시된 한자의 뜻·획수·오행, 수리 계산, 오행 상생/상극 관계 등이 실제 사실에 부합하는가
   - 1: 핵심 사실 오류 다수 / 3: 일부 오류 있으나 대체로 맞음 / 5: 사실 오류 없음

2. groundedness (근거성)
   - 답변이 구체적 한자 정보(뜻·획수·오행)나 수리 계산 근거를 포함하는가 (막연한 추천이 아닌가)
   - 1: 근거 없이 이름만 나열 / 3: 일부 근거 제시 / 5: 모든 추천에 구체적 근거 포함

3. answer_relevance (답변 관련성)
   - 생성된 답변이 사용자 질문에 실질적으로 답하는가
   - 1: 질문과 무관한 답변 / 3: 부분적으로 답변 / 5: 질문에 완전하고 명확하게 답변

출력 형식 (JSON만, 다른 텍스트 절대 금지):
{
  "factual_accuracy": <1-5>,
  "groundedness": <1-5>,
  "answer_relevance": <1-5>,
  "reason": "<3가지 지표에 대한 간략한 근거 1~2문장>"
}"""


def _judge_single(query: str, answer: str) -> dict:
    content = (
        f"[사용자 질문]\n{query}\n\n"
        f"[모델 답변]\n{answer}"
    )
    try:
        resp = _judge.invoke([
            SystemMessage(content=_JUDGE_SYSTEM),
            HumanMessage(content=content),
        ])
        raw = re.sub(r"```json\s*|\s*```", "", resp.content.strip()).strip()
        parsed = json.loads(raw)
        return {
            "factual_accuracy":  int(parsed.get("factual_accuracy", 0)),
            "groundedness":      int(parsed.get("groundedness", 0)),
            "answer_relevance":  int(parsed.get("answer_relevance", 0)),
            "reason":            parsed.get("reason", ""),
        }
    except Exception as e:
        return {"factual_accuracy": 0, "groundedness": 0, "answer_relevance": 0, "reason": f"[평가 오류: {e}]"}


def call_qwen(query: str, llm: ChatOpenAI) -> tuple[str, float]:
    t0 = time.time()
    try:
        resp = llm.invoke([HumanMessage(content=query)])
        return resp.content.strip(), time.time() - t0
    except Exception as e:
        return f"[오류: {e}]", time.time() - t0


# ─────────────────────────────────────────────
# 평가 실행
# ─────────────────────────────────────────────
def run_eval(case_num: int | None, llm: ChatOpenAI) -> list[dict]:
    cases = EVAL_CASES if case_num is None else [c for c in EVAL_CASES if c[0] == case_num]
    if not cases:
        print(f"[오류] 케이스 {case_num}번이 없습니다.")
        return []

    results = []
    sep = "─" * 60

    print(f"\n{'='*60}")
    print(f" Qwen LoRA LLM-as-a-Judge 평가  (Judge: gpt-5.4)")
    print(f" 평가 케이스: {len(cases)}개  |  방식: 직접 호출 (RAG 없음)")
    print(f"{'='*60}")

    for num, desc, query in cases:
        print(f"\n▶ [{num}] {desc}")
        print(f"   질문: {query}")

        answer, elapsed = call_qwen(query, llm)
        print(f"   {elapsed:.1f}s")

        if answer.startswith("[오류"):
            print(f"   [SKIP] {answer}")
            results.append({
                "case": num, "desc": desc, "query": query, "answer": answer,
                "factual_accuracy": 0, "groundedness": 0, "answer_relevance": 0,
                "reason": "답변 생성 실패", "elapsed": elapsed,
            })
            continue

        scores = _judge_single(query, answer)
        print(f"   {sep}")
        print(f"   Factual Accuracy  : {scores['factual_accuracy']}/5")
        print(f"   Groundedness      : {scores['groundedness']}/5")
        print(f"   Answer Relevance  : {scores['answer_relevance']}/5")
        print(f"   근거: {scores['reason']}")

        results.append({
            "case": num, "desc": desc, "query": query,
            "answer": answer, "elapsed": elapsed,
            **scores,
        })

    return results


def print_summary(results: list[dict]):
    if not results:
        return

    valid = [r for r in results if r["factual_accuracy"] > 0]
    if not valid:
        print("\n[경고] 유효한 평가 결과가 없습니다.")
        return

    avg_fa = sum(r["factual_accuracy"] for r in valid) / len(valid)
    avg_gr = sum(r["groundedness"]     for r in valid) / len(valid)
    avg_ar = sum(r["answer_relevance"] for r in valid) / len(valid)
    avg_total = (avg_fa + avg_gr + avg_ar) / 3

    print(f"\n{'='*60}")
    print(f" 평가 결과 요약  (유효 케이스: {len(valid)}/{len(results)}개)")
    print(f"{'='*60}")
    print(f" {'지표':<22} {'평균':>6}  {'분포 (케이스별)'}")
    print(f" {'─'*56}")

    for label, key in [
        ("Factual Accuracy",  "factual_accuracy"),
        ("Groundedness",      "groundedness"),
        ("Answer Relevance",  "answer_relevance"),
    ]:
        avg = sum(r[key] for r in valid) / len(valid)
        dist = " ".join(str(r[key]) for r in results)
        print(f" {label:<22} {avg:>5.2f}  [{dist}]")

    print(f" {'─'*56}")
    print(f" {'종합 평균':<22} {avg_total:>5.2f}")
    print(f"{'='*60}\n")

    print(f" {'#':<3} {'설명':<16} {'FA':>3} {'GR':>3} {'AR':>3} {'평균':>5}")
    print(f" {'─'*44}")
    for r in results:
        fa = r["factual_accuracy"]
        gr = r["groundedness"]
        ar = r["answer_relevance"]
        avg = (fa + gr + ar) / 3 if fa > 0 else 0
        flag = " ⚠️" if avg < 3 and fa > 0 else ""
        print(f" {r['case']:<3} {r['desc']:<16} {fa:>3} {gr:>3} {ar:>3} {avg:>5.2f}{flag}")
    print()


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qwen LoRA LLM-as-a-Judge 평가")
    parser.add_argument("--case",   "-c", type=int,  default=None, help="평가할 케이스 번호 (미지정 시 전체)")
    parser.add_argument("--output", "-o", type=str,  default=None, help="결과 저장 경로 (JSON)")
    parser.add_argument("--url",    "-u", type=str,  default=None, help="Qwen 서버 base URL (예: https://xxx.trycloudflare.com/v1)")
    args = parser.parse_args()

    qwen_url = args.url
    if not qwen_url:
        qwen_url = os.getenv("QWEN_API_BASE")
    if not qwen_url:
        print("[오류] Qwen 서버 URL이 필요합니다. --url 옵션 또는 QWEN_API_BASE 환경변수를 설정하세요.")
        sys.exit(1)

    print(f"Qwen 서버: {qwen_url}")
    llm = ChatOpenAI(
        model="qwen-lora",
        api_key="empty",
        base_url=qwen_url,
        temperature=0.7,
        max_tokens=4096,
        timeout=120,
    )

    results = run_eval(args.case, llm)
    print_summary(results)

    if args.output and results:
        out_path = args.output
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"결과 저장: {out_path}")
