"""
title: Qwen3.5-4B SFT 파인튜닝 스크립트 (v2)
description: RunPod 등 GPU 환경에서 QLoRA를 활용한 파인튜닝 (안전한 데이터 필터링 포함)
"""

import os
import json
import torch
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer, SFTConfig

MODEL_ID = "Qwen/Qwen3.5-4B"
DATA_DIR = "../data/processed"
OUTPUT_DIR = "./models/qwen3.5-4b-naming-qlora-v2"

def load_and_filter_datasets():
    """
    data/processed 폴더 내의 json 파일들을 읽어들여 학습 가능한 (instruction, output 구조) 
    데이터로 통합합니다. 
    기존 SFT 데이터(finetune_data_v2) 외에도, 한자, 법령, 수리 등 RAG용 지식 문서(document)를 
    자동으로 질의응답(QA) 형태로 변환하여 모델이 지식을 직접 학습하도록 지원합니다.
    """
    valid_data = []
    
    if not os.path.exists(DATA_DIR):
        print(f"❌ 데이터 경로를 찾을 수 없습니다: {DATA_DIR}")
        return valid_data
        
    for filename in os.listdir(DATA_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(DATA_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    if not isinstance(data, list):
                        continue
                        
                    for item in data:
                        # 1. 이미 QA 형태인 경우 (finetune_data_v2.json)
                        if "instruction" in item and "output" in item:
                            valid_data.append({
                                "instruction": item["instruction"],
                                "input": item.get("input", ""),
                                "output": item["output"]
                            })
                        # 2. RAG 지식 문서 형태인 경우 (한자, 법령, 오행 등 7개 JSON) -> QA로 동적 변환
                        elif "document" in item and "metadata" in item:
                            meta = item["metadata"]
                            doc_type = meta.get("type", "지식")
                            
                            # 한자 데이터 (약 9,000건)
                            if doc_type == "hanja":
                                hanja_char = meta.get("hanja", "")
                                instruction = f"인명용 한자 '{hanja_char}'의 뜻, 획수, 오행 정보를 알려줘."
                            # 법령 데이터 (약 250건)
                            elif doc_type == "law":
                                instruction = f"가족관계의 등록 등에 관한 법률 및 인명용 한자 관련 조항을 설명해줘."
                            # 수리/오행 등 기타 지식 (약 400건)
                            else:
                                title = meta.get("title", meta.get("collection", "작명 지식"))
                                instruction = f"{title}에 대해 설명해줘."
                                
                            valid_data.append({
                                "instruction": instruction,
                                "input": "",
                                "output": item["document"]
                            })
            except Exception as e:
                print(f"⚠️ {filename} 읽기 오류: {e}")
                
    return valid_data

def format_chatml(example):
    """
    ChatML 형식으로 포맷팅합니다.
    """
    prompt = f"<|im_start|>user\n{example['instruction']}"
    if example['input']:
        prompt += f"\n{example['input']}"
    prompt += f"<|im_end|>\n<|im_start|>assistant\n{example['output']}<|im_end|>"
    return {"text": prompt}

def main():
    print("🚀 데이터 로딩 및 검증 시작...")
    raw_data = load_and_filter_datasets()
    
    if not raw_data:
        print("❌ 유효한 파인튜닝 데이터가 없습니다. 스크립트를 종료합니다.")
        return
        
    print(f"✅ 총 {len(raw_data)}건의 유효한 학습 데이터를 찾았습니다.")
    
    dataset = Dataset.from_list(raw_data)
    dataset = dataset.map(format_chatml)
    
    print("🚀 토크나이저 및 모델 로딩...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    # Qwen은 pad_token 설정이 중요
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        device_map="auto",
        torch_dtype=torch.bfloat16
    )
    # 메모리 최적화
    model.gradient_checkpointing_enable()

    print("🚀 LoRA 어댑터 설정 중...")
    lora_config = LoraConfig(
        r=16, # 기존 8에서 16으로 증가 (복잡한 작명 로직 학습력 강화)
        lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"], # 타겟 모듈 확장
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM
    )
    
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    
    training_args = SFTConfig(
        output_dir=OUTPUT_DIR,
        dataset_text_field="text",
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        learning_rate=2e-4,
        num_train_epochs=3,
        logging_steps=10,
        save_strategy="epoch",
        optim="adamw_torch_fused", # 최신 PyTorch Fused 옵티마이저 (속도 향상)
        fp16=False,
        bf16=True, # Runpod RTX 3000/4000 시리즈 이상에서 권장
        max_grad_norm=0.3,
        warmup_ratio=0.03,
        lr_scheduler_type="cosine", # Cosine decay
    )
    
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=training_args,
        processing_class=tokenizer,
    )
    
    print("🔥 파인튜닝 학습을 시작합니다!")
    trainer.train()
    
    print("💾 어댑터 모델 저장 중...")
    trainer.model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"🎉 모든 작업이 완료되었습니다! 모델 저장 경로: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
