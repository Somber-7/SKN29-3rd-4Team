from fastapi import FastAPI, Request
from pydantic import BaseModel
import torch
import uvicorn
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import time

app = FastAPI()

# 모델과 토크나이저 전역 변수 설정
model = None
tokenizer = None

def load_model():
    global model, tokenizer
    BASE_MODEL_ID = "Qwen/Qwen3.5-4B"
    LORA_PATH = "./models/qwen3.5-4b-naming-qlora"

    print("🚀 서버 시작 중: 토크나이저 및 기본 모델 로딩...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID)
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_ID,
        dtype=torch.bfloat16,
        device_map="auto"
    )
    print("🔗 파인튜닝된 뇌(LoRA)를 기본 모델에 장착 중...")
    model = PeftModel.from_pretrained(base_model, LORA_PATH)
    model.eval()
    print("✅ 모델 로딩 완료! 서버가 클라이언트의 요청을 기다립니다.")

@app.on_event("startup")
async def startup_event():
    load_model()

# LangChain ChatOpenAI 구조와 호환되도록 요청/응답 모델 정의
class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "qwen-lora"
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int = 250

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    # LangChain이 보낸 메시지 배열을 하나의 문자열(ChatML 포맷)로 변환
    prompt = ""
    for msg in request.messages:
        if msg.role == "system":
            prompt += f"<|im_start|>system\n{msg.content}<|im_end|>\n"
        elif msg.role == "user":
            prompt += f"<|im_start|>user\n{msg.content}<|im_end|>\n"
        elif msg.role == "assistant":
            prompt += f"<|im_start|>assistant\n{msg.content}<|im_end|>\n"
    
    # 마지막은 항상 assistant의 차례
    if not prompt.endswith("<|im_start|>assistant\n"):
        prompt += "<|im_start|>assistant\n"

    print(f"\n[서버 수신] 랭그래프 요청: {prompt}")

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=0.9,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id
        )

    # 새로 생성된 답변만 자르기
    generated_text = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
    print(f"[서버 송신] 응답: {generated_text[:50]}...")

    # OpenAI API 형식에 맞게 리턴 (LangChain이 이 형식을 기대함)
    response = {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": generated_text
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": inputs.input_ids.shape[1],
            "completion_tokens": len(outputs[0]) - inputs.input_ids.shape[1],
            "total_tokens": len(outputs[0])
        }
    }
    
    return response

if __name__ == "__main__":
    # 포트 8000으로 오픈하여 외부에서 proxy.runpod.net으로 접근 가능하게 설정
    uvicorn.run(app, host="0.0.0.0", port=8000)
