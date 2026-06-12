import os
import re
import json
import warnings
from pathlib import Path

# PyKoSpacing 경고 무시
warnings.filterwarnings("ignore", category=UserWarning, module="pykospacing")

try:
    import pdfplumber
except ImportError:
    print("pdfplumber가 설치되지 않았습니다. 먼저 'pip install pdfplumber'를 실행해주세요.")
    exit(1)

try:
    from pykospacing import Spacing
    spacing = Spacing()
except ImportError:
    print("pykospacing 모듈이 설치되지 않았습니다.")
    spacing = None

# 경로 설정
ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = ROOT_DIR / "data" / "raw" / "pdf"
PROCESSED_DATA_DIR = ROOT_DIR / "data" / "processed"
OUTPUT_FILE = PROCESSED_DATA_DIR / "paper_documents.json"

# 실제 논문 메타데이터 매핑
PAPER_METADATA = {
    "韓國人 이름 짓기에서 性別을 구분 짓는 音節 特性과 分布에 관한 연구.pdf": {
        "title": "한국인 이름 짓기에서 성별을 구분 짓는 음절 특성과 분포에 관한 연구",
        "author": "정예진·이찬규",
        "year": "2025",
        "type": "paper"
    },
    "최근 한국인 남녀 이름의 성차와 변화에 대한 사회음운론적 고찰.pdf": {
        "title": "최근 한국인 남녀 이름의 성차와 변화에 대한 사회음운론적 고찰",
        "author": "이서라·강현석",
        "year": "2023",
        "type": "paper"
    },
    "한국인 이름의 최근 동향 및 특징에 관한 사회명명학적 연구.pdf": {
        "title": "한국인 이름의 최근 동향 및 특징에 관한 사회명명학적 연구",
        "author": "강현석·이서라",
        "year": "2025",
        "type": "paper"
    }
}

HANJA_PATTERN = re.compile(r'[\u4e00-\u9fff]+')

def apply_kospacing_with_hanja_protection(text):
    if not spacing:
        return text

    hanja_blocks = []
    
    def replacer(match):
        hanja = match.group(0)
        hanja_blocks.append(hanja)
        return f"__HANJA_{len(hanja_blocks)-1}__"

    protected_text = HANJA_PATTERN.sub(replacer, text)
    spaced_text = spacing(protected_text)
    
    for i, hanja in enumerate(hanja_blocks):
        spaced_text = spaced_text.replace(f"__HANJA_{i}__", hanja)
        
    return spaced_text

def table_to_markdown(table_data):
    """
    2차원 리스트를 마크다운 표 문자열로 변환합니다.
    """
    if not table_data or not table_data[0]:
        return ""
        
    md_lines = []
    for i, row in enumerate(table_data):
        cleaned_row = [str(cell).replace('\n', ' ') if cell is not None else "" for cell in row]
        row_str = "| " + " | ".join(cleaned_row) + " |"
        md_lines.append(row_str)
        
        if i == 0:
            separator = "| " + " | ".join(["---"] * len(cleaned_row)) + " |"
            md_lines.append(separator)
            
    return "\n".join(md_lines)

def in_bbox(obj_bbox, tables_bboxes):
    """
    주어진 bbox가 하나라도 테이블 bbox 내에 겹치는지 판별합니다.
    bbox = (x0, top, x1, bottom)
    """
    x0, top, x1, bottom = obj_bbox
    for t_bbox in tables_bboxes:
        tx0, ttop, tx1, tbottom = t_bbox
        # 겹치지 않는 조건의 여집합 = 겹침
        if not (x1 <= tx0 or x0 >= tx1 or bottom <= ttop or top >= tbottom):
            return True
    return False

def process_pdf(pdf_path, needs_spacing=False):
    chunks = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            
            # 1. 테이블 탐색 및 추출
            tables = page.find_tables()
            table_bboxes = [table.bbox for table in tables]
            
            extracted_tables = page.extract_tables()
            for md_table_data in extracted_tables:
                md_str = table_to_markdown(md_table_data)
                if md_str.strip():
                    chunks.append({
                        "content": md_str,
                        "chunk_type": "table",
                        "page_number": page_num
                    })
            
            # 2. 본문 텍스트 추출 (테이블 영역 제외 필터링)
            def filter_out_tables(obj):
                if 'x0' in obj and 'top' in obj and 'x1' in obj and 'bottom' in obj:
                    return not in_bbox((obj['x0'], obj['top'], obj['x1'], obj['bottom']), table_bboxes)
                return True
                
            filtered_page = page.filter(filter_out_tables)
            raw_text = filtered_page.extract_text()
            if not raw_text:
                continue
                
            # 3. 본문 텍스트 청킹 (기존 500자 로직 적용)
            paragraphs = re.split(r'\n\s*\n', raw_text)
            merged_paragraphs = []
            buffer = ""
            
            for p in paragraphs:
                clean_p = p.replace('\n', ' ').strip()
                if not clean_p:
                    continue
                if len(clean_p) < 50:
                    buffer += clean_p + " "
                else:
                    if buffer:
                        merged_paragraphs.append(buffer + clean_p)
                        buffer = ""
                    else:
                        merged_paragraphs.append(clean_p)
            if buffer:
                if merged_paragraphs:
                    merged_paragraphs[-1] += " " + buffer.strip()
                else:
                    merged_paragraphs.append(buffer.strip())

            final_paragraphs = []
            for p in merged_paragraphs:
                if len(p) > 500:
                    sentences = re.split(r'(?<=[.?!])\s+', p)
                    current_chunk = ""
                    for s in sentences:
                        if len(current_chunk) + len(s) > 500 and current_chunk:
                            final_paragraphs.append(current_chunk.strip())
                            current_chunk = s + " "
                        else:
                            current_chunk += s + " "
                    if current_chunk.strip():
                        final_paragraphs.append(current_chunk.strip())
                else:
                    final_paragraphs.append(p)
            
            # 4. 텍스트 교정 및 청크 저장
            for chunk_text in final_paragraphs:
                if needs_spacing:
                    chunk_text = apply_kospacing_with_hanja_protection(chunk_text)
                
                chunks.append({
                    "content": chunk_text,
                    "chunk_type": "text",
                    "page_number": page_num
                })
                
    return chunks

def main():
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_documents = []

    for pdf_filename, meta in PAPER_METADATA.items():
        pdf_path = RAW_DATA_DIR / pdf_filename
        if not pdf_path.exists():
            print(f"파일 없음: {pdf_path}")
            continue

        print(f"\n[추출 시작] {pdf_filename}")
        
        # 텍스트 샘플링으로 공백 비율 계산
        sample_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:5]:
                extracted = page.extract_text()
                if extracted:
                    sample_text += extracted
        
        total_len = len(sample_text)
        space_count = sample_text.count(" ")
        space_ratio = (space_count / total_len * 100) if total_len > 0 else 100
        needs_spacing = space_ratio < 5.0
        
        if needs_spacing:
            print(f"-> 공백 비율 {space_ratio:.2f}% (5% 미만): pykospacing 가동")
        else:
            print(f"-> 공백 비율 {space_ratio:.2f}%: 기본 유지")

        chunks = process_pdf(pdf_path, needs_spacing=needs_spacing)
        print(f"-> {len(chunks)}개 청크(표+본문) 생성 완료.")
        
        for c in chunks:
            doc = {
                "content": c["content"],
                "metadata": {
                    "source": pdf_filename,
                    "title": meta["title"],
                    "author": meta["author"],
                    "year": meta["year"],
                    "type": meta["type"],
                    "chunk_type": c["chunk_type"],
                    "page_number": c["page_number"]
                }
            }
            all_documents.append(doc)

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_documents, f, ensure_ascii=False, indent=2)

    print(f"\n총 {len(all_documents)}개의 청크가 성공적으로 생성되어 {OUTPUT_FILE}에 저장되었습니다.")

if __name__ == "__main__":
    main()
