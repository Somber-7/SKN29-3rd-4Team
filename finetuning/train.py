"""
LoRA 파인튜닝 — Qwen/Qwen3.5-4B
실행: python train.py
환경: CUDA GPU (VRAM 12GB 이상 권장, RTX 4000 Ada 최적화)
"""
import json
import torch
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer, SFTConfig

MODEL_ID = "Qwen/Qwen3.5-4B"
DATA_PATH = "finetune_data.json"
OUTPUT_DIR = "./models/qwen3.5-4b-naming-qlora"

# 1. 데이터 로드 및 포맷팅 (ChatML 형식)
with open(DATA_PATH, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

formatted_data = []
for item in raw_data:
    prompt = f"<|im_start|>user\n{item['instruction']}<|im_end|>\n<|im_start|>assistant\n{item['output']}<|im_end|>"
    formatted_data.append({"text": prompt})

dataset = Dataset.from_list(formatted_data)

# 2. 토크나이저 로드
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
tokenizer.pad_token = tokenizer.eos_token

# 3. 모델 로드 (최신 dtype 파라미터 적용)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    dtype=torch.bfloat16, 
    device_map="auto",
)

# 4. LoRA 설정 
lora_config = LoraConfig(
    r=8,
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type=TaskType.CAUSAL_LM
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# 5. SFTConfig 설정 (에러를 유발하는 선택적 파라미터 전면 제거)
training_args = SFTConfig(
    output_dir=OUTPUT_DIR,
    dataset_text_field="text",
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    num_train_epochs=3,
    logging_steps=5,
    save_strategy="epoch",
    optim="adamw_torch",
    fp16=False,
    bf16=True, 
    max_grad_norm=0.3,
    warmup_steps=10, 
    lr_scheduler_type="constant",
)

trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    args=training_args,
    processing_class=tokenizer,
)

# 6. 학습 시작
print("🚀 본격적인 파인튜닝 학습을 시작합니다!")
trainer.train()

# 7. 어댑터 저장
trainer.model.save_pretrained(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)
print(f"✅ 모델 저장 완료: {OUTPUT_DIR}")
