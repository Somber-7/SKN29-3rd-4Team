import os
import json
import asyncio
import aiohttp
import sys
from dotenv import load_dotenv

# Windows 콘솔 인코딩 에러 방지
sys.stdout.reconfigure(encoding='utf-8')

# 환경 변수 로드
load_dotenv()
API_KEY = os.getenv("URIMALSAM_API_KEY")

# 경로 설정
INPUT_PATH = r"data\processed\ocr\dictionary_database.json"
OUTPUT_PATH = r"data\processed\ocr\ocr_hallucination_report.json"

# 동시성 제어 (공공 API이므로 초당 요청 수를 보수적으로 제한)
SEMAPHORE = asyncio.Semaphore(10)

async def check_word_existence(session, word: str, original_entry: dict) -> dict:
    """우리말샘 API를 호출하여 단어의 존재 여부를 확인합니다."""
    url = "https://opendict.korean.go.kr/api/search"
    params = {
        "key": API_KEY,
        "q": word,
        "req_type": "json",
        "method": "exact",
        "advanced": "y",
        "target": "1", # 표제어 검색
        # "type2": "native" -> 고유어뿐만 아니라 일반 명사 오인식도 잡기 위해 제한을 품
    }
    
    async with SEMAPHORE:
        for attempt in range(3): # 최대 3회 재시도
            try:
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status != 200:
                        await asyncio.sleep(2)
                        continue
                        
                    # Urimalsam API might return wrong Content-Type
                    text = await response.text()
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        print(f"JSON Parsing Error for {word}. Text: {text[:100]}")
                        return {"status": "error", "entry": original_entry, "reason": "JSON_ERROR"}
                    
                    # API 에러 코드 반환 시 (키 오류 등)
                    if "error" in data:
                        print(f"[API 에러] {word}: {data['error'].get('message')}")
                        return {"status": "error", "entry": original_entry, "reason": "API_ERROR"}
                        
                    total = int(data.get("channel", {}).get("total", 0))
                    
                    if total == 0:
                        return {"status": "hallucination", "entry": original_entry, "reason": "NOT_FOUND"}
                    else:
                        return {"status": "valid", "entry": original_entry, "reason": "FOUND"}
                        
            except Exception as e:
                print(f"Exception for {word}: {repr(e)}")
                await asyncio.sleep(2)
                continue
                
        # 3회 모두 실패한 경우
        return {"status": "error", "entry": original_entry, "reason": "TIMEOUT_OR_NETWORK_ERROR"}

async def main():
    if not API_KEY or "여기에_우리말샘_API_키" in API_KEY:
        print("💡 에러: .env 파일에 URIMALSAM_API_KEY가 설정되지 않았습니다. API 키를 먼저 입력해 주세요!")
        return

    if not os.path.exists(INPUT_PATH):
        print(f"💡 에러: 입력 파일이 없습니다. ({INPUT_PATH}) 먼저 하이브리드 파서를 실행해 주세요.")
        return

    with open(INPUT_PATH, 'r', encoding='utf-8') as f:
        database = json.load(f)
        
    print(f"🔍 총 {len(database)}개의 단어에 대해 우리말샘 교차 검증을 시작합니다...")
    print("공공 API 서버 보호를 위해 천천히 조회합니다. (예상 소요 시간: 3~5분)")

    hallucinations = []
    api_errors = []
    valid_count = 0

    # aiohttp 세션을 공유하여 연결 속도 최적화
    async with aiohttp.ClientSession() as session:
        tasks = [check_word_existence(session, entry['word'], entry) for entry in database]
        
        # 결과를 기다리며 진행률 간단히 출력
        for i, future in enumerate(asyncio.as_completed(tasks), 1):
            result = await future
            
            if result["status"] == "hallucination":
                hallucinations.append(result["entry"])
            elif result["status"] == "valid":
                valid_count += 1
            else:
                api_errors.append(result["entry"])
                
            if i % 500 == 0:
                print(f"진행 상황: {i} / {len(database)} 완료...")

    print("\n" + "="*50)
    print(f"✅ 검증 완료! (총 {len(database)}개)")
    print(f"🟢 정상 단어: {valid_count}개")
    print(f"🔴 환각 의심 단어(사전 미등록): {len(hallucinations)}개")
    print(f"🟡 API 통신 오류 단어: {len(api_errors)}개")
    print("="*50)

    # 리포트 저장 (환각 의심 단어들만 모아서 저장)
    report_data = {
        "summary": {
            "total_checked": len(database),
            "valid_words": valid_count,
            "hallucinated_words": len(hallucinations),
            "api_errors": len(api_errors)
        },
        "hallucinations": hallucinations,
        "api_errors": api_errors
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
        
    print(f"\n📂 환각 의심 리포트가 저장되었습니다: {OUTPUT_PATH}")
    print("이 리포트에 있는 단어들만 원본 PDF와 대조하여 수동으로 수정하시면 됩니다!")

if __name__ == "__main__":
    asyncio.run(main())
