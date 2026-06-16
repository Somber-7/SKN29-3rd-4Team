import json
import urllib.request
import os
import time
import random
import concurrent.futures
import re

API_URL = "http://192.168.0.42:9099/v1/chat/completions"
MODEL = "naming_pipeline"

def clean_output(text):
    text = text.replace("[원본 추천 결과]\n", "").replace("[원본 추천 결과]", "")
    text = re.sub(r'\[참고 정보\].*?(?=---|\Z)', '', text, flags=re.DOTALL)
    return text.strip()

def call_pipeline(instruction):
    data = json.dumps({
        "messages": [{"role": "user", "content": instruction}],
        "model": MODEL
    }).encode('utf-8')
    
    req = urllib.request.Request(API_URL, data=data, headers={'Content-Type': 'application/json'})
    
    try:
        res = urllib.request.urlopen(req)
        output_chunks = []
        for line in res:
            line = line.decode('utf-8').strip()
            if line.startswith("data: "):
                chunk_data = line[len("data: "):]
                if chunk_data == "[DONE]":
                    break
                try:
                    chunk_json = json.loads(chunk_data)
                    delta = chunk_json["choices"][0].get("delta", {})
                    if "content" in delta:
                        output_chunks.append(delta["content"])
                except:
                    pass
        raw_output = "".join(output_chunks)
        return clean_output(raw_output)
    except Exception as e:
        print(f"Error calling pipeline for instruction: {instruction}\nError: {e}")
        return None

def append_data():
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed"))
    output_path = os.path.join(output_dir, "finetune_data.json")
    
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            dataset = json.load(f)
            # 잘못 추가됐을 수 있는 데이터를 방지하기 위해 앞에서 만든 50건까지만 짜름
            if len(dataset) > 50:
                dataset = dataset[:50]
    else:
        dataset = []

    queries = []
    
    surnames_hanja = {"김":"金", "이":"李", "박":"朴", "최":"崔", "정":"鄭", "강":"姜", "조":"趙", "윤":"尹"}
    genders = ["남자아이", "여자아이"]
    
    # 1. 순우리말 이름 (20건) - 성은 O야 형식 추가!
    for _ in range(20):
        kor_surname = random.choice(list(surnames_hanja.keys()))
        hanja_surname = surnames_hanja[kor_surname]
        gender = random.choice(genders)
        query = f"{kor_surname}씨 성을 가진 {gender} 순우리말(한글) 이름 1개를 추천해줘. 성은 {hanja_surname}야"
        queries.append(query)
        
    # 2. 외자(한 글자) 한자 이름 (10건) - 성은 O야 형식 포함!
    for _ in range(10):
        kor_surname = random.choice(list(surnames_hanja.keys()))
        hanja_surname = surnames_hanja[kor_surname]
        gender = random.choice(genders)
        query = f"{kor_surname}씨 성을 가진 {gender} 외자(한 글자) 한자 이름 1개를 추천해줘. 성은 {hanja_surname}야"
        queries.append(query)
            
    print(f"포맷 수정 완료! 순우리말 20건, 외자 10건(총 30건) 파이프라인 수집 시작...")
    
    new_items_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_to_query = {executor.submit(call_pipeline, q): q for q in queries}
        for future in concurrent.futures.as_completed(future_to_query):
            query = future_to_query[future]
            try:
                result = future.result()
                if result and "Rate limit reached" not in result:
                    dataset.append({"instruction": query, "input": "", "output": result})
                    new_items_count += 1
                    print(f"[추가 성공] {query[:25]}...")
                elif result and "Rate limit reached" in result:
                    print(f"[실패] 429 Rate Limit - {query[:25]}...")
                time.sleep(1.0)
            except Exception as exc:
                print(f"[실패] {query[:25]}... 예외 발생: {exc}")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
        
    print(f"\n포맷 수정된 데이터에 순우리말/외자 이름 {new_items_count}건이 추가되었습니다! (현재 총 데이터 건수: {len(dataset)}건)")

if __name__ == "__main__":
    append_data()
