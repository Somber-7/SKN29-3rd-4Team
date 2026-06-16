import os
import json
import argparse
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
from tqdm import tqdm

def load_model():
    BASE_MODEL_ID = "Qwen/Qwen3.5-4B"
    LORA_PATH = "./models/qwen3.5-4b-naming-qlora"

    print("🚀 토크나이저 및 기본 모델 로딩 중... (잠시만 기다려주세요)")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID)

    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_ID,
        dtype=torch.bfloat16,
        device_map="auto"
    )

    print("🔗 파인튜닝된 뇌(LoRA)를 기본 모델에 장착 중...")
    model = PeftModel.from_pretrained(base_model, LORA_PATH)
    model.eval()
    
    return model, tokenizer

def run_interactive(model, tokenizer):
    print("\n=========================================")
    print("인터랙티브 테스트 모드입니다. 질문을 입력하세요. (종료하려면 'quit' 또는 'exit' 입력)")
    print("=========================================")
    
    while True:
        prompt = input("\n❓ 질문: ")
        if prompt.strip().lower() in ['quit', 'exit']:
            print("테스트를 종료합니다.")
            break
            
        chat_format = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        inputs = tokenizer(chat_format, return_tensors="pt").to(model.device)

        print("\n🤖 Qwen이 작명 중입니다...\n")
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=250,
                temperature=0.7,
                top_p=0.9,
                repetition_penalty=1.1,
                pad_token_id=tokenizer.eos_token_id
            )

        generated_text = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        print(generated_text)
        print("=========================================")

def run_evaluate(model, tokenizer, num_samples):
    import evaluate
    
    print("🚀 평가지표(BLEU, ROUGE) 모듈 다운로드 중...")
    bleu = evaluate.load("bleu")
    rouge = evaluate.load("rouge")

    DATA_PATH = "finetune_data.json"

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    test_samples = dataset[:num_samples]
    predictions = []
    references = []

    print(f"\n총 {len(test_samples)}개의 데이터에 대해 문장 생성 테스트를 시작합니다...")
    for item in tqdm(test_samples):
        prompt = item["instruction"]
        ground_truth = item["output"]
        references.append(ground_truth)
        
        chat_format = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        inputs = tokenizer(chat_format, return_tensors="pt").to(model.device)
        
        with torch.no_grad():
            outputs = model.generate(
                **inputs, 
                max_new_tokens=150, 
                temperature=0.1, 
                pad_token_id=tokenizer.eos_token_id
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
                        help="실행 모드: 'interactive'(눈으로 직접 확인) 또는 'evaluate'(정량 지표 계산)")
    parser.add_argument("--samples", type=int, default=20,
                        help="evaluate 모드에서 평가할 샘플 개수 (기본값: 20)")
    
    args = parser.parse_args()
    
    model, tokenizer = load_model()
    
    if args.mode == "interactive":
        run_interactive(model, tokenizer)
    elif args.mode == "evaluate":
        run_evaluate(model, tokenizer, args.samples)
