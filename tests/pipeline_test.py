# -*- coding: utf-8 -*-
"""
pipeline_test.py — 작명 QA 시스템 파이프라인 테스트

사용법:
  python pipeline_test.py              # 프리셋 테스트 케이스 전부 실행
  python pipeline_test.py --interactive # 직접 질문 입력 모드
  python pipeline_test.py --case 3     # 프리셋 3번만 실행
"""
import sys
import os
import time
import argparse

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "mcp"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "graph"))

# ─────────────────────────────────────────────
# 프리셋 테스트 케이스
# ─────────────────────────────────────────────
PRESETS = [
    # (번호, 설명, 질문)
    (1,  "한자 수리 복합",    "김씨(8획) 성에 어울리는 吉수 획수 조합과 밝은 뜻의 木오행 한자를 추천해줘"),
    (2,  "오행 상생 설명",    "木火土 오행 조합이 상생인지 알려줘"),
    (3,  "순우리말 이름",     "밝고 아름다운 뜻의 순우리말 이름 3개 추천해줘"),
    (4,  "법령 조문",         "인명용 한자에 관한 법령을 찾아줘"),
    (5,  "논문/트렌드",       "최근 한국 이름 트렌드와 음절 특성 연구 결과를 알려줘"),
    (6,  "그래프 DB 탐색",    "목 오행 상생 상극 관계를 Neo4j 그래프로 알려줘"),
    (7,  "이름 수리 계산",    "성씨 획수 8, 이름 첫째 7획 둘째 6획일 때 81수리 4격을 계산해줘"),
    (8,  "통합 추천",         "최씨 성에 火오행이고 吉수이며 지혜로운 뜻의 한자 이름을 추천해줘"),
]


# ─────────────────────────────────────────────
# 파이프라인 실행
# ─────────────────────────────────────────────
def run_pipeline(query: str, app, verbose: bool = True) -> dict:
    """단일 질문을 파이프라인에 흘려보내고 결과를 반환한다."""
    init_state = {
        "query":       query,
        "context":     "",
        "next_action": "generate",
        "answer":      "",
        "iterations":  0,
        "used_tools":  [],
        "collections": [],
    }

    t0 = time.time()
    result = app.invoke(init_state)
    elapsed = time.time() - t0

    if verbose:
        sep = "─" * 56
        print(f"\n{sep}")
        print(f"[질문]  {query}")
        print(f"[도구]  {result.get('used_tools', [])} | 반복 {result.get('iterations', 0)}회 | {elapsed:.1f}s")
        print(f"[답변]\n{result.get('answer', '').strip()}")
        print(sep)

    return result


def run_preset(case_num: int | None, app):
    """프리셋 케이스 실행."""
    cases = PRESETS if case_num is None else [c for c in PRESETS if c[0] == case_num]
    if not cases:
        print(f"[오류] 케이스 {case_num}번이 없습니다.")
        return

    print(f"\n{'='*56}")
    print(f" 작명 QA 파이프라인 테스트  ({len(cases)}개 케이스)")
    print(f"{'='*56}")

    pass_count = 0
    for num, desc, query in cases:
        print(f"\n▶ [{num}] {desc}")
        result = run_pipeline(query, app)
        answer = result.get("answer", "").strip()
        if answer and not answer.startswith("[오류]"):
            pass_count += 1

    print(f"\n{'='*56}")
    print(f" 결과: {pass_count}/{len(cases)} 케이스 답변 생성 성공")
    print(f"{'='*56}\n")


def run_interactive(app):
    """직접 입력 모드."""
    print("\n" + "=" * 56)
    print(" 작명 QA 인터랙티브 모드  (종료: q 또는 quit)")
    print("=" * 56)
    while True:
        try:
            query = input("\n질문: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query:
            continue
        if query.lower() in {"q", "quit", "exit", "종료"}:
            break
        run_pipeline(query, app)


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="작명 QA 파이프라인 테스트")
    parser.add_argument("--interactive", "-i", action="store_true", help="인터랙티브 입력 모드")
    parser.add_argument("--case",        "-c", type=int, default=None, help="실행할 프리셋 번호 (미지정 시 전체)")
    args = parser.parse_args()

    print("그래프 빌드 중...")
    from naming_graph import build_graph
    app = build_graph()
    print("준비 완료.\n")

    if args.interactive:
        run_interactive(app)
    else:
        run_preset(args.case, app)
