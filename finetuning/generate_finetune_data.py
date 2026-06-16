import json
import urllib.request
import os
import time
import random
import re

API_URL = "http://192.168.0.42:9099/v1/chat/completions"
MODEL = "naming_pipeline"

def clean_output(text):
    """
    파이프라인 RAG 내부 처리 과정에서 유출된 프롬프트나 참고 정보를 제거합니다.
    """
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
        return clean_output("".join(output_chunks))
    except Exception as e:
        return f"Error: {e}"

def deduplicate_dataset(dataset):
    """중복된 이름이 추천된 결과물 필터링"""
    unique_data = []
    seen_names = set()
    for item in dataset:
        # 응답 내용에서 이름 추출 시도
        match = re.search(r"## \[이름 1\]\s*([가-힣]+)", item['output'])
        if match:
            name = match.group(1)
            if name in seen_names: 
                continue
            seen_names.add(name)
        unique_data.append(item)
    return unique_data

def generate_finetune_data():
    dataset = []
    
    # =========================================================
    # 1. 고정 질의 (수리 4격 및 대법원 규칙 QA) - 총 20건
    # =========================================================
    fixed_queries = []
    
    # 수리 계산
    strokes = [(8, 10, 12), (7, 9, 11), (5, 8, 14), (10, 10, 10), (6, 12, 12), (4, 5, 6), (15, 12, 8), (9, 9, 9), (11, 13, 15), (8, 15, 5)]
    for s1, s2, s3 in strokes:
        fixed_queries.append(f"이름 한자의 획수가 각각 {s1}획, {s2}획, {s3}획일 때 수리 4격을 계산해줘.")
        
    # 오행 및 법령 QA
    qa_queries = [
        "대법원 인명용 한자 규칙에 대해 설명해줘.", "수리 4격이 무엇인지 알려줘.",
        "오행에서 목생화의 의미는 뭐야?", "대법원 인명용 한자에 등록되지 않은 한자를 이름에 쓸 수 있어?",
        "오행 상극에 대해 설명해줘.", "한글 이름도 가족관계등록부에 올릴 수 있나요?",
        "오행 상생의 종류를 모두 말해줘.", "이름 한자의 획수는 어떻게 계산하나요?",
        "수리 4격 중 원격은 어떤 시기의 운을 의미하나요?", "수리 4격 중 정격은 무엇을 의미하나요?"
    ]
    fixed_queries.extend(qa_queries)
    
    # =========================================================
    # 2. 랜덤 작명 질의 생성 (다양한 성씨와 성별 조합) - 약 330건
    # =========================================================
    random_queries = []
    surnames_hanja = {
        "김":"金", "이":"李", "박":"朴", "최":"崔", "정":"鄭", "강":"姜", "조":"趙", "윤":"尹",
        "장":"張", "임":"林", "한":"韓", "오":"吳", "서":"徐", "신":"申", "권":"權", "황":"黃",
        "안":"安", "송":"宋", "전":"全", "홍":"洪", "유":"柳", "고":"高", "문":"文", "양":"梁"
    }
    
    for _ in range(330):
        kor = random.choice(list(surnames_hanja.keys()))
        han = surnames_hanja[kor]
        gender = random.choice(["남자아이", "여자아이"])
        q_type = random.choices(["normal", "korean", "single"], weights=[60, 20, 20])[0]
        
        if q_type == "normal": 
            query = f"{kor}씨 성을 가진 {gender} 한자 이름 1개를 추천해줘. 성은 {han}야"
        elif q_type == "korean": 
            query = f"{kor}씨 성을 가진 {gender} 순우리말(한글) 이름 1개를 추천해줘. 성은 {han}야"
        else: 
            query = f"{kor}씨 성을 가진 {gender} 외자(한 글자) 한자 이름 1개를 추천해줘. 성은 {han}야"
            
        random_queries.append(query)
        
    all_queries = fixed_queries + random_queries
    total_target = len(all_queries)
    print(f"🚀 총 {total_target}건의 파인튜닝용 통합 데이터셋 생성을 시작합니다...")

    # =========================================================
    # 3. 파이프라인 호출 및 저장
    # =========================================================
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "processed"))
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "finetune_data.json")

    success_count = 0
    for idx, query in enumerate(all_queries, 1):
        result = call_pipeline(query)
        
        if result and "Rate limit" not in result and "Error" not in result:
            dataset.append({"instruction": query, "input": "", "output": result})
            success_count += 1
            if idx % 10 == 0:
                print(f"[{idx}/{total_target}] 수집 진행 중... (현재 유효 건수: {success_count})")
        else:
            print(f"[{idx}/{total_target}] API 지연 발생 (Rate Limit). 3초 대기합니다...")
            time.sleep(3.0)
            
        time.sleep(0.5) # 서버 부하 방지용 딜레이
        
        # 50개마다 중간 저장
        if idx % 50 == 0:
            temp_data = deduplicate_dataset(dataset)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(temp_data, f, ensure_ascii=False, indent=2)

    # 최종 중복 제거 및 저장
    print(f"\n✅ 수집 완료! 최종 중복 검사를 시작합니다.")
    final_dataset = deduplicate_dataset(dataset)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_dataset, f, ensure_ascii=False, indent=2)
        
    print(f"🎉 모든 작업이 끝났습니다! 저장된 최종 파인튜닝 데이터 건수: {len(final_dataset)}건")
    print(f"저장 경로: {output_path}")

if __name__ == "__main__":
    generate_finetune_data()
