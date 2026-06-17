# sLLM 파인튜닝 전략

## 현행 구현 기준 보완 (2026-06-16)

> 문서 상태: sLLM 실험 전략 문서. 운영 서비스 모델과 파인튜닝 실험 모델을 분리해서 읽어야 한다.

현재 운영 Pipeline은 Open WebUI에서 전달된 질문을 `naming_pipeline.py`가 수신한 뒤 LangGraph Router와 MCP Tool 계층을 거쳐 `gpt-5.4-mini`로 답변을 생성하는 구조다. 이 문서의 Qwen 기반 파인튜닝은 운영 기본 모델을 대체하기 위한 절차가 아니라, 작명 응답 형식 학습과 평가 항목 검증을 위한 별도 실험 트랙으로 관리한다.

| 항목 | 현재 기준 |
|---|---|
| 운영 서비스 모델 | `gpt-5.4-mini` |
| sLLM 실험 모델 | `Qwen/Qwen3.5-4B` |
| 학습 데이터 | `data/processed/finetune_data.json` 296건 |
| 관련 코드 | `finetuning/train.py`, `serve_api.py`, `run_inference.py`, `eval.sh` |
| 역할 | 평가 항목 충족 및 작명 포맷 학습 실험. 운영 기본 답변 생성 모델은 아님 |

현재 저장소 코드와 이 문서의 실행 예시는 `Qwen/Qwen3.5-4B` 기준으로 맞춘다.

> 작성일: 2026-06-12
> 대상 모델: Qwen3.5:4b
> 기법: QLoRA (PEFT)
> 목적: 평가계획서 항목 충족 + 파인튜닝 효과 측정

---

## 1. 목적 및 위치

프로덕션 서비스에는 gpt-5.4-mini를 사용한다. Qwen3.5:4b 파인튜닝은 **평가 항목 충족**을 위한 별도 실험 트랙이다.

### 평가계획서 관련 항목

| 항목 | 배점 | 내용 |
|---|---|---|
| sLLM 파인튜닝 코드 역량 | 일부 | QLoRA 코드가 메모리 효율성을 고려해 올바르게 작성되었는가 |
| LM-Eval 벤치마크 | 일부 | 파인튜닝 전후 LM-Eval 수치 비교 |

---

## 2. 데이터셋 구성 전략

### 형식: CoT QA

```json
{
  "instruction": "임씨 성을 가진 남자아이 한자 이름 1개를 추천해줘.",
  "input": "",
  "output": "## [이름 1] 임준혁 (林俊赫)\n**추천 이유**: ...\n**한자 풀이**:\n- 俊(준) — 준수하다, 14획 [火오행]\n- 赫(혁) — 빛나다, 14획 [火오행]\n**오행 흐름**: 木(임) → 火(준) → 火(혁) — 상생\n**수리**: 원격28격(吉), 형격28격(吉)\n---\n⚠️ 면책 고지: ..."
}
```

### 데이터 소스

| 소스 | 활용 방법 |
|---|---|
| ChromaDB (`hanja_col`) | 한자 획수·오행 정보 → output의 한자 풀이 근거 |
| `db_server.calculate_name_suri()` | 수리 4격 → output의 수리 항목 |
| `db_server.lookup_ohaeng_combo()` | 오행 조합 → 상생/상극 판단 |
| 기존 테스트 결과 | 정상 출력된 답변 재활용 |

### 목표 수량

| 유형 | 건수 |
|---|---|
| 한자 이름 추천 (성씨·성별·개수 조합) | 30건 |
| 수리 계산 질문 | 10건 |
| 오행·법령 질문 | 10건 |
| **합계** | **50건** |

50건은 소규모 파인튜닝에 충분한 최소 양. 증상 개선 확인용이며 SOTA 목표 아님.

---

## 3. QLoRA 설정

```python
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer, SFTConfig

# 4비트 양자화
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype="bfloat16",
    bnb_4bit_use_double_quant=True,
)

# LoRA 설정
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=8,
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
)

# 학습 설정
training_args = SFTConfig(
    output_dir="./models/qwen3.5-4b-naming-qlora",
    num_train_epochs=3,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    fp16=True,
    save_strategy="epoch",
    logging_steps=10,
    max_seq_length=2048,
)
```

### 메모리 요구사항

| 구성 | VRAM |
|---|---|
| 4비트 양자화 + LoRA r=8 | ~6GB |
| 배치 사이즈 2 + gradient_accumulation 4 | ~8GB |

RTX 3080 (10GB) 또는 강의실 GPU 서버에서 실행 가능.

---

## 4. 평가 방법

### 4-1. LM-Eval 벤치마크 (평가계획서 항목)

파인튜닝 전/후 일반 언어 능력 변화 측정.

```bash
# 파인튜닝 전 (base)
lm_eval --model hf \
    --model_args pretrained=Qwen/Qwen3.5-4B \
    --tasks hellaswag,arc_easy,arc_challenge \
    --device cuda:0 \
    --output_path ./eval_results/base

# 파인튜닝 후 (finetuned)
lm_eval --model hf \
    --model_args pretrained=./models/qwen3.5-4b-naming-qlora \
    --tasks hellaswag,arc_easy,arc_challenge \
    --device cuda:0 \
    --output_path ./eval_results/finetuned
```

**주의**: LM-Eval은 일반 언어 능력을 측정한다. 도메인 특화 성능(작명 품질)과 직접 연관되지 않음. 발표 시 이 점을 명확히 설명할 것.

### 4-2. 도메인 QA 평가 (선택)

Ground Truth QA 30개에 대해 파인튜닝 전/후 LLM-as-a-Judge 점수 비교.

| 지표 | 측정 방법 |
|---|---|
| 형식 준수율 | `## [이름 N]` 패턴 매칭 |
| 할루시네이션 비율 | 수동 검토 or GPT-4o-mini Judge |
| Answer Relevance | LLM-as-a-Judge (0~1 스코어) |

---

## 5. 발표 스토리라인

> "프로덕션 서비스는 gpt-5.4-mini를 사용해 안정적인 품질을 확보했습니다.
> 동시에 Qwen3.5:4b에 QLoRA 파인튜닝을 적용해 도메인 특화 sLLM 실험을 진행했습니다.
> LM-Eval 결과에서 파인튜닝 전후 [수치] 변화를 확인했으며,
> 실제 작명 QA에서 형식 준수율이 [X%] → [Y%]로 개선되었습니다."

---

## 6. 일정 (발표: 2026-06-17)

| 날짜 | 작업 | 상태 |
|---|---|---|
| 06-13 | QA 데이터셋 50건 구성 | ✅ 완료 |
| 06-14 | QLoRA 파인튜닝 실행 | 🔄 진행 중 |
| 06-15 | LM-Eval + 도메인 QA 평가 | 🔄 진행 중 |
| 06-16 | 결과 정리 및 발표 자료 삽입 | 🔄 진행 중 |
| 06-17 | 발표 | — |
