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
    # 새로 파인튜닝된 v2 어댑터 경로
    ADAPTER_DIR = "./models/qwen3.5-4b-naming-qlora-v2"

    print("🚀 서버 시작 중: 토크나이저 및 베이스 모델 로딩...")
    tokenizer = AutoTokenizer.from_pretrained(ADAPTER_DIR)
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_ID,
        torch_dtype=torch.bfloat16,
        device_map="auto"
    )
    print("🔗 파인튜닝된 뇌(LoRA v2)를 베이스 모델에 장착 중...")
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
    model.eval()
    print("✅ 모델 로딩 완료! 서버가 클라이언트의 요청을 기다립니다.")

@app.on_event("startup")
async def startup_event():
    load_model()

# LangChain / OpenAI API 구조와 호환되도록 요청/응답 모델 정의
class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "qwen-lora"
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int = 8192

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    # LangChain이 보낸 메시지 객체를 딕셔너리로 변환하여 apply_chat_template 사용
    messages_dict = [{"role": msg.role, "content": msg.content} for msg in request.messages]
    
    text = tokenizer.apply_chat_template(
        messages_dict,
        tokenize=False,
        add_generation_prompt=True
    )

    print(f"\n[서버 수신] 파이프라인 요청 프롬프트:\n{text}")

    inputs = tokenizer([text], return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            inputs.input_ids,
            max_new_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.eos_token_id,
            do_sample=True
        )

    # 새로 생성된 답변만 자르기
    generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in zip(inputs.input_ids, outputs)]
    generated_text = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
    
    print(f"[서버 송신] 응답 내용: {generated_text[:100]}...")

    # OpenAI API 형식에 맞게 리턴 (LangChain/OpenWebUI가 이 형식을 기대함)
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
    # 포트 8000으로 오픈하여 외부에서 RunPod Proxy URL로 접근 가능하게 설정
    uvicorn.run(app, host="0.0.0.0", port=8000)
