# sLLM 파인튜닝 전략

> 작성일: 2026-06-12 / 수정일: 2026-06-16
> 대상 모델: `Qwen/Qwen2.5-3B-Instruct` (Ollama 태그: `qwen2.5:3b`)
> 기법: QLoRA (PEFT + TRL)
> 목적: 평가계획서 항목 충족 + 파인튜닝 효과 측정

---

## 1. 목적 및 위치

프로덕션 서비스에는 GPT-4o-mini를 사용한다. Qwen 파인튜닝은 **평가 항목 충족**을 위한 별도 실험 트랙이다. 메인 파이프라인에 통합하지 않는다.

### 평가계획서 관련 항목

| 항목 | 내용 |
|---|---|
| sLLM 파인튜닝 코드 역량 | QLoRA 코드가 메모리 효율성을 고려해 올바르게 작성되었는가 |
| LM-Eval 벤치마크 | 파인튜닝 전후 LM-Eval 수치 비교 |

---

## 2. 전체 워크플로우

```
[로컬 — 지금 즉시]
  1. QA 데이터셋 50건 생성 → finetune_data.json
     (ChromaDB + db_server 활용, Neo4j는 선택)

[RunPod — GPU 서버]
  2. 파일 업로드: finetune_data.json + train.py
  3. HuggingFace에서 Qwen/Qwen2.5-3B-Instruct 다운로드
  4. QLoRA 파인튜닝 실행 (~1~2시간, A100 기준)
  5. LM-Eval 벤치마크 실행
  6. 결과 다운로드

[로컬 — 발표 전]
  7. 결과 정리 → 발표 자료 삽입
```

RunPod 업로드 크기: `finetune_data.json` (수십 KB) + `train.py` — 대용량 데이터 불필요.

---

## 3. 데이터셋 구성

### 형식: Alpaca (instruction-input-output)

```json
{
  "instruction": "임씨 성을 가진 남자아이 한자 이름 1개를 추천해줘.",
  "input": "",
  "output": "## [이름 1] 임준혁 (林俊赫)\n**추천 이유**: 오행 상생 흐름이 자연스럽고 획수 균형이 좋습니다.\n**한자 풀이**:\n- 俊(준) — 준수하다, 14획 [火오행]\n- 赫(혁) — 빛나다, 14획 [火오행]\n**오행 흐름**: 木(임) → 火(준) → 火(혁) — 상생\n**수리**: 원격28격(吉), 형격28격(吉)\n---\n⚠️ 면책 고지: 이름 추천은 참고용이며 최종 결정은 보호자가 하시기 바랍니다."
}
```

### 목표 수량: 50건

| 유형 | 건수 |
|---|---|
| 한자 이름 추천 (성씨·성별·개수 조합) | 30건 |
| 수리 계산 질문 | 10건 |
| 오행·법령 질문 | 10건 |
| **합계** | **50건** |

### 생성 방법

- 기존 파이프라인 테스트 결과를 `finetune_data.json`으로 변환
- `src/data/` 스크립트로 ChromaDB · db_server 데이터 직접 활용
- 생성 위치: `data/processed/finetune_data.json`

---

## 4. QLoRA 학습 코드 (`train.py`)

RunPod에 업로드할 완성 스크립트:

```python
"""
QLoRA 파인튜닝 — Qwen/Qwen2.5-3B-Instruct
실행: python train.py
환경: CUDA GPU 필수 (VRAM 8GB 이상)
"""
import json
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer, SFTConfig

MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"
DATA_PATH = "finetune_data.json"
OUTPUT_DIR = "./models/qwen2.5-3b-naming-qlora"

# 데이터 로드
with open(DATA_PATH, encoding="utf-8") as f:
    raw = json.load(f)

def format_prompt(example):
    return {
        "text": f"### 질문\n{example['instruction']}\n\n### 답변\n{example['output']}"
    }

dataset = Dataset.from_list(raw).map(format_prompt)

# 4비트 양자화
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype="bfloat16",
    bnb_4bit_use_double_quant=True,
)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    quantization_config=bnb_config,
    device_map="auto",
)
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
tokenizer.pad_token = tokenizer.eos_token

# LoRA 설정
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=8,
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# 학습
training_args = SFTConfig(
    output_dir=OUTPUT_DIR,
    num_train_epochs=3,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    bf16=True,
    save_strategy="epoch",
    logging_steps=10,
    max_seq_length=2048,
    dataset_text_field="text",
)

trainer = SFTTrainer(
    model=model,
    args=training_args,
    train_dataset=dataset,
)
trainer.train()
trainer.save_model(OUTPUT_DIR)
print(f"모델 저장 완료: {OUTPUT_DIR}")
```

### 메모리 요구사항

| 구성 | VRAM |
|---|---|
| 4비트 양자화 + LoRA r=8 | ~6GB |
| 배치 사이즈 2 + gradient_accumulation 4 | ~8GB |

RunPod 권장: **RTX 3090 (24GB)** 또는 **A100 40GB** (여유 있게 실행 가능)

### RunPod 패키지 설치

```bash
pip install transformers peft trl datasets bitsandbytes accelerate
```

---

## 5. 평가

### 5-1. LM-Eval 벤치마크 (평가계획서 항목)

```bash
pip install lm_eval

# 파인튜닝 전 (base)
lm_eval --model hf \
    --model_args pretrained=Qwen/Qwen2.5-3B-Instruct \
    --tasks hellaswag,arc_easy,arc_challenge \
    --device cuda:0 \
    --batch_size 4 \
    --output_path ./eval_results/base

# 파인튜닝 후 (finetuned)
lm_eval --model hf \
    --model_args pretrained=./models/qwen2.5-3b-naming-qlora \
    --tasks hellaswag,arc_easy,arc_challenge \
    --device cuda:0 \
    --batch_size 4 \
    --output_path ./eval_results/finetuned
```

> **주의**: LM-Eval은 일반 언어 능력을 측정한다. 도메인 특화 성능(작명 품질)과 직접 연관되지 않으므로 발표 시 이 점을 명확히 설명할 것.

### 5-2. 도메인 QA 평가 (선택)

파인튜닝 전/후 작명 QA 30건에 대해 LLM-as-a-Judge 점수 비교.

| 지표 | 측정 방법 |
|---|---|
| 형식 준수율 | `## [이름 N]` 패턴 매칭 |
| 할루시네이션 비율 | GPT-4o-mini Judge |
| Answer Relevance | LLM-as-a-Judge (0~1 스코어) |

---

## 6. 일정 (발표: 2026-06-17)

| 날짜 | 작업 |
|---|---|
| **06-16 (오늘)** | QA 50건 생성 → RunPod 업로드 → 파인튜닝 실행 → LM-Eval |
| **06-17 오전** | 결과 다운로드 → 수치 정리 → 발표 자료 삽입 |
| **06-17 발표** | 발표 |

---

## 7. 발표 스토리라인

> "프로덕션 서비스는 GPT-4o-mini를 사용해 안정적인 품질을 확보했습니다.
> 동시에 Qwen2.5-3B-Instruct에 QLoRA 파인튜닝을 적용해 도메인 특화 sLLM 실험을 진행했습니다.
> LM-Eval 결과에서 파인튜닝 전후 [수치] 변화를 확인했으며,
> 실제 작명 QA에서 형식 준수율이 [X%] → [Y%]로 개선되었습니다."
