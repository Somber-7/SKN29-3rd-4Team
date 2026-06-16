import json
import random
import os

def load_json(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_data():
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed")
    hanja_db = load_json(os.path.join(base_dir, "hanja_documents.json"))
    suri_db = load_json(os.path.join(base_dir, "suri_documents.json"))
    law_db = load_json(os.path.join(base_dir, "law_articles.json"))
    
    # 필터링
    valid_hanja = [h['metadata'] for h in hanja_db if h['metadata'].get('is_person_name_hanja', False)]
    suri_list = [s['metadata'] for s in suri_db if 'suri_num' in s['metadata']]
    
    data = []
    
    # 1. 한자 이름 추천 (30건)
    surnames = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
    for _ in range(30):
        surname = random.choice(surnames)
        h1, h2 = random.sample(valid_hanja, 2)
        
        name = h1['hangul'] + h2['hangul']
        instruction = f"{surname}씨 성을 가진 아이 한자 이름 1개를 추천해줘."
        
        output = (
            f"## [이름 1] {surname}{name}\n"
            f"**추천 이유**: 로컬 DB 기반 오행 상생 흐름과 획수 균형을 고려한 이름입니다.\n"
            f"**한자 풀이**:\n"
            f"- {h1['hanja']}({h1['hangul']}) — {h1.get('sound_meaning', '')}, {h1.get('strokes', 0)}획 [{h1.get('sound_ohaeng', '')}오행]\n"
            f"- {h2['hanja']}({h2['hangul']}) — {h2.get('sound_meaning', '')}, {h2.get('strokes', 0)}획 [{h2.get('sound_ohaeng', '')}오행]\n"
            f"**오행 흐름**: {h1.get('sound_ohaeng', '')} → {h2.get('sound_ohaeng', '')}\n"
            f"---\n"
            f"⚠️ 면책 고지: 이름 추천은 참고용이며 최종 결정은 보호자가 하시기 바랍니다."
        )
        data.append({"instruction": instruction, "input": "", "output": output})
        
    # 2. 수리 계산 질문 (10건)
    for _ in range(10):
        suri_item = random.choice(suri_list)
        suri_num = suri_item.get('suri_num', 10)
        fortune = suri_item.get('fortune_ko', '보통')
        gyeok = suri_item.get('gyeok_ko', '')
        
        # 획수 합이 suri_num이 되도록 임의 분할 (단순화)
        s1 = suri_num // 2
        s2 = suri_num - s1
        
        instruction = f"이름 한자의 획수가 합해서 {suri_num}획일 때 수리 운세(격)를 설명해줘."
        output = (
            f"수리 총합 {suri_num}획에 대한 설명입니다.\n"
            f"- **격(格)**: {gyeok}\n"
            f"- **운세**: {fortune}\n"
            f"---\n"
            f"⚠️ 수리 4격은 성명학의 참고 지표 중 하나입니다."
        )
        data.append({"instruction": instruction, "input": "", "output": output})
        
    # 3. 오행·법령 질문 (10건)
    for _ in range(10):
        law_item = random.choice(law_db)
        title = law_item.get('title', '규칙')
        raw_text = law_item.get('raw_text', '')
        
        instruction = f"가족관계등록법령(또는 대법원 규칙)에서 '{title}'에 대해 설명해줘."
        output = f"관련 법령 및 규칙의 내용은 다음과 같습니다.\n\n{raw_text}\n\n---\n⚠️ 제공된 정보는 성명학 및 관련 법령에 기반한 참고 정보입니다."
        data.append({"instruction": instruction, "input": "", "output": output})

    random.shuffle(data)
    
    output_path = os.path.join(base_dir, "finetune_data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print(f"Generated {len(data)} items based on local DB to {output_path}")

if __name__ == "__main__":
    generate_data()
