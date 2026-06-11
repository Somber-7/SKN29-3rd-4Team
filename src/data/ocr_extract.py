"""
국립국어원_정겨운우리말.pdf 전체 OCR 추출 스크립트

PDF 구조:
  idx  0~ 2  (p1  ~p3  ) : 표지, 인가 도장         → 건너뜀
  idx  3~335 (p4  ~p336 ) : 본문 (단일 컬럼)        → 단일 OCR
  idx 336~338 (p337~p339) : 섹션 구분, 빈 면        → 건너뜀
  idx 339~405 (p340~p406) : 색인 (2단 컬럼)        → 좌/우 분리 OCR

출력:
  data/processed/ocr/ocr_raw_full.txt   : 페이지 구분자 포함 전체 텍스트
  data/processed/ocr/ocr_progress.json  : 진행 상태 (중단 후 재개용)
"""

import os
import sys

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import fitz          # PyMuPDF
import easyocr
import json
import numpy as np
from PIL import Image
import io

# ── 경로 설정 ──────────────────────────────────────────────
PDF_PATH    = r"D:\prj0617\SKN29-3rd-4Team\docs\국립국어원_정겨운우리말.pdf"
OUTPUT_DIR  = r"D:\prj0617\SKN29-3rd-4Team\data\processed\ocr"
RAW_TXT     = os.path.join(OUTPUT_DIR, "ocr_raw_full.txt")
PROGRESS_F  = os.path.join(OUTPUT_DIR, "ocr_progress.json")

# ── 페이지 범위 설정 ───────────────────────────────────────
SKIP_PAGES   = set(range(0, 3)) | set(range(336, 339))   # 건너뛸 페이지 인덱스
INDEX_START  = 339                                         # 2단 컬럼 색인 시작 인덱스
DPI          = 200                                         # 이미지 해상도

def load_progress():
    if os.path.exists(PROGRESS_F):
        with open(PROGRESS_F, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"done": []}

def save_progress(done_list):
    with open(PROGRESS_F, "w", encoding="utf-8") as f:
        json.dump({"done": done_list}, f)

def page_to_numpy(page, dpi=DPI):
    """PDF 페이지 → numpy 배열 (RGB)"""
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("png")
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return np.array(img)

def ocr_single(reader, img_np):
    """단일 이미지 OCR → 줄 단위 텍스트 리스트"""
    results = reader.readtext(img_np, detail=0, paragraph=True)
    return results

def ocr_two_column(reader, img_np):
    """2단 컬럼 이미지: 좌/우 절반으로 분리해서 각각 OCR"""
    h, w = img_np.shape[:2]
    mid = w // 2
    left  = img_np[:, :mid]
    right = img_np[:, mid:]
    left_text  = reader.readtext(left,  detail=0, paragraph=True)
    right_text = reader.readtext(right, detail=0, paragraph=True)
    # 좌 컬럼 전체 → 우 컬럼 전체 순서로 합침
    return left_text + right_text

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 진행 상태 로드 (중단 후 재개 지원)
    progress = load_progress()
    done_set = set(progress["done"])

    print("EasyOCR 한국어 모델 로딩 중... (첫 실행 시 모델 다운로드 약 1~2분 소요)")
    reader = easyocr.Reader(["ko"], gpu=False)
    print("모델 로딩 완료\n")

    doc   = fitz.open(PDF_PATH)
    total = len(doc)
    print(f"총 페이지: {total}")
    print(f"건너뛸 페이지: {sorted(SKIP_PAGES)}")
    print(f"2단 컬럼 시작 인덱스: {INDEX_START} (p{INDEX_START+1})\n")

    # 출력 파일 — 재개 시 append, 처음 시작 시 write
    mode = "a" if done_set else "w"
    out_file = open(RAW_TXT, mode, encoding="utf-8")

    try:
        for idx in range(total):

            if idx in done_set:
                continue

            if idx in SKIP_PAGES:
                done_set.add(idx)
                continue

            page    = doc[idx]
            img_np  = page_to_numpy(page)
            page_no = idx + 1

            try:
                if idx >= INDEX_START:
                    lines = ocr_two_column(reader, img_np)
                    section = "색인"
                else:
                    lines = ocr_single(reader, img_np)
                    section = "본문"

                text = "\n".join(lines)

                out_file.write(f"\n=== PAGE {page_no} [{section}] ===\n")
                out_file.write(text + "\n")
                out_file.flush()

                done_set.add(idx)
                save_progress(sorted(done_set))

                print(f"[{page_no:>3}/{total}] {section} | {len(lines):>3}줄 추출")

            except Exception as e:
                print(f"[{page_no:>3}/{total}] 오류 발생 — {e}")
                out_file.write(f"\n=== PAGE {page_no} [오류: {e}] ===\n")
                out_file.flush()

    finally:
        out_file.close()
        doc.close()

    print(f"\n완료. 결과 저장: {RAW_TXT}")

if __name__ == "__main__":
    main()
