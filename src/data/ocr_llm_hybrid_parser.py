import os
import sys
import re
import json
import asyncio
from typing import List, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

# Windows 콘솔 인코딩 에러 방지
sys.stdout.reconfigure(encoding='utf-8')

# 환경 변수 로드
load_dotenv()

# OpenAI 비동기 클라이언트 초기화
api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=api_key)

# 경로 설정
INPUT_PATH = r"data\processed\ocr\ocr_cleaned.txt"
OUTPUT_PATH = r"data\processed\ocr\dictionary_database.json"

# ── 1. Pydantic 스키마 ──────────────────────────────────
class ParsedDefinition(BaseModel):
    definition: str = Field(description="교정된 뜻풀이 텍스트 (표제어 및 예문 제외)")
    example: Optional[str] = Field(description="교정된 예문 텍스트 (없으면 null). 예문 기호 ¶는 포함하지 마세요.")

# ── 2. 정규식 패턴 ──────────────────────────────────────────────
# 본문 전체에서 "가납사니 [명]" 패턴을 모두 찾아내는 정규식
# 품사 태그가 확실한 것들만 매칭하여 오작동 방지
HEADWORD_PATTERN = re.compile(r"([가-힣]+)\s*\[(명|동|형|부|관|수|대|감|의존명사|접사)\]")

# ── 3. 동시성 제어 ──────────────────────────────────────
SEMAPHORE = asyncio.Semaphore(15)

@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(Exception)
)
async def process_text_block(word: str, pos: str, raw_text: str) -> dict:
    async with SEMAPHORE:
        if not raw_text.strip():
            return {"word": word, "pos": pos, "definition": "", "example": None}

        system_prompt = (
            f"너는 국어사전 교정기야. 지금 교정할 항목의 표제어는 '{word}'이고 품사는 '{pos}'야.\n"
            f"이 표제어 '{word}'는 절대 오타로 취급해서 다른 단어로 고치면 안 돼. 예문 안에서도 무조건 원형을 보존해.\n"
            f"주어진 텍스트는 뜻풀이와 예문이 섞여 있어. 문맥을 보고 오탈자(농고->놓고 등)를 자연스럽게 교정하되,\n"
            f"'이름', '틀리다', '눈', '논' 등 내용어는 절대 건드리지 마.\n"
            f"텍스트를 분석하여 JSON 스키마에 맞춰 '뜻풀이'와 '예문'으로 깔끔하게 분리해서 반환해."
        )

        response = await client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text}
            ],
            response_format=ParsedDefinition,
            temperature=0.0,
        )

        parsed_data = response.choices[0].message.parsed
        return {
            "word": word,
            "pos": pos,
            "definition": parsed_data.definition,
            "example": parsed_data.example
        }

async def main():
    if not os.path.exists(INPUT_PATH):
        print(f"Error: 입력 파일이 없습니다. ({INPUT_PATH})")
        return

    with open(INPUT_PATH, 'r', encoding='utf-8') as f:
        full_text = f.read()

    # 페이지 헤더 등 쓰레기 텍스트 1차 제거
    full_text = re.sub(r"=== PAGE.*?===", "", full_text)

    entries = []
    print("1단계: 전체 텍스트에서 표제어 격리 및 블록 청킹 중...")
    
    matches = list(HEADWORD_PATTERN.finditer(full_text))
    
    for i in range(len(matches)):
        word = matches[i].group(1)
        pos = matches[i].group(2)
        start_idx = matches[i].end()
        
        if i + 1 < len(matches):
            end_idx = matches[i+1].start()
        else:
            end_idx = len(full_text)
            
        raw_text = full_text[start_idx:end_idx].strip()
        
        # 쓰레기 데이터 건너뛰기
        if len(word) > 10: 
            continue
            
        entries.append({
            "word": word,
            "pos": pos,
            "raw_text": raw_text
        })

    print(f"총 {len(entries)}개의 단어 블록 분할 완료.")

    if not api_key or "여기에_API_키를_입력해주세요" in api_key:
        print("💡 알림: .env 파일에 OpenAI API Key가 설정되지 않아 파싱까지만 수행하고 LLM 통신은 생략합니다.")
        return

    print("2단계: LLM 비동기 세탁 시작 (Rate Limit 방어용 Semaphore & Tenacity 적용)...")
    
    # 디버깅/테스트용: 너무 많아서 돈이 많이 나오거나 오래 걸릴 수 있으므로 
    # 일단 5300개 전체를 돌리되 실패 로그를 상세히 찍습니다.
    tasks = [
        process_text_block(entry['word'], entry['pos'], entry['raw_text']) 
        for entry in entries
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_database = []
    failed_llm = 0
    for res in results:
        if isinstance(res, Exception):
            failed_llm += 1
            print(f"LLM 처리 중 에러 발생: {res}")
        else:
            final_database.append(res)

    print(f"3단계: 결과물 JSON 저장 중... (LLM 통신 에러 {failed_llm}건)")
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_database, f, ensure_ascii=False, indent=2)

    print(f"✨ 완료! 성공적으로 변환된 데이터: {len(final_database)}개")

if __name__ == "__main__":
    asyncio.run(main())
