"""
title: Qwen3.5-4B 파인튜닝 모델 추론 및 평가 스크립트 (v2)
description: 학습이 완료된 QLoRA 어댑터를 불러와서 직접 터미널에서 채팅하거나 BLEU/ROUGE 평가를 진행합니다.
"""

import os
import json
import argparse
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
from tqdm import tqdm

# ==========================================================
# 1. 설정
# ==========================================================
BASE_MODEL_ID = "Qwen/Qwen3.5-4B"
ADAPTER_DIR = "./models/qwen3.5-4b-naming-qlora-v2"
DATA_PATH = "../data/processed/finetune_data_v2.json"

def load_model():
    print("🚀 토크나이저 및 베이스 모델 로딩 중...")
    
    tokenizer = AutoTokenizer.from_pretrained(ADAPTER_DIR)
    
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_ID,
        device_map="auto",
        torch_dtype=torch.bfloat16,
    )
    
    print("🚀 LoRA 어댑터 결합 중...")
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
    model.eval()
    
    return model, tokenizer

def run_interactive(model, tokenizer):
    print("\n✅ 모델 로딩 완료! 작명 AI 어시스턴트와 대화를 시작합니다.")
    print("종료하려면 'quit', 'exit', '종료' 중 하나를 입력하세요.\n")
    
    while True:
        try:
            user_input = input("🧑 사용자: ")
            if user_input.strip() in ["quit", "exit", "종료"]:
                print("대화를 종료합니다.")
                break
                
            if not user_input.strip():
                continue
            
            messages = [{"role": "user", "content": user_input}]
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
            
            generated_ids = model.generate(
                model_inputs.input_ids,
                max_new_tokens=512,
                temperature=0.7,
                top_p=0.9,
                repetition_penalty=1.05,
                pad_token_id=tokenizer.eos_token_id,
                do_sample=True
            )
            
            generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)]
            response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
            
            print(f"\n🤖 Qwen 작명 AI: \n{response}\n")
            print("-" * 50)
            
        except KeyboardInterrupt:
            print("\n대화를 종료합니다.")
            break
        except Exception as e:
            print(f"\n오류가 발생했습니다: {e}\n")

def run_evaluate(model, tokenizer, num_samples):
    try:
        import evaluate
    except ImportError:
        print("❌ 'evaluate' 라이브러리가 필요합니다. 'pip install evaluate rouge_score' 명령어를 실행해주세요.")
        return
        
    print("🚀 평가지표(BLEU, ROUGE) 모듈 다운로드 중...")
    bleu = evaluate.load("bleu")
    rouge = evaluate.load("rouge")

    if not os.path.exists(DATA_PATH):
        print(f"❌ 데이터 경로를 찾을 수 없습니다: {DATA_PATH}")
        return

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # QA 형식이 있는 데이터만 추출
    dataset = [item for item in dataset if "instruction" in item and "output" in item]
    test_samples = dataset[:num_samples]
    predictions = []
    references = []

    print(f"\n총 {len(test_samples)}개의 데이터에 대해 문장 생성 테스트를 시작합니다...")
    for item in tqdm(test_samples):
        prompt = item["instruction"]
        ground_truth = item["output"]
        references.append(ground_truth)
        
        messages = [{"role": "user", "content": prompt}]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors="pt").to(model.device)
        
        with torch.no_grad():
            outputs = model.generate(
                inputs.input_ids, 
                max_new_tokens=250, 
                temperature=0.1, 
                pad_token_id=tokenizer.eos_token_id,
                do_sample=True
            )
        
        generated_text = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        predictions.append(generated_text)

    print("\n📊 평가 지표 계산 중...")
    rouge_results = rouge.compute(predictions=predictions, references=references)
    bleu_results = bleu.compute(predictions=predictions, references=references)

    print("\n==========================================")
    print("🏆 문장 생성 평가 지표 결과 (BLEU / ROUGE)")
    print("==========================================")
    print(f"ROUGE-1: {rouge_results['rouge1']:.4f} (단어 일치도)")
    print(f"ROUGE-2: {rouge_results['rouge2']:.4f} (2단어 연속 일치도)")
    print(f"ROUGE-L: {rouge_results['rougeL']:.4f} (문장 구조 유사도)")
    print(f"BLEU 스코어: {bleu_results['bleu']:.4f} (정답과의 번역 품질 유사도)")
    print("==========================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="파인튜닝 모델 추론 및 평가 툴")
    parser.add_argument("--mode", type=str, choices=["interactive", "evaluate"], default="interactive",
                        help="실행 모드: 'interactive'(눈으로 직접 확인) 또는 'evaluate'(BLEU/ROUGE 정량 평가)")
    parser.add_argument("--samples", type=int, default=20,
                        help="evaluate 모드에서 평가할 샘플 개수 (기본값: 20)")
    
    args = parser.parse_args()
    
    model, tokenizer = load_model()
    
    if args.mode == "interactive":
        run_interactive(model, tokenizer)
    elif args.mode == "evaluate":
        run_evaluate(model, tokenizer, args.samples)
