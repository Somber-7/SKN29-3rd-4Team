# OCR 데이터 수집 및 정제 파이프라인

## 개요

**대상 문서**: 국립국어원_정겨운우리말.pdf (406페이지)  
**목적**: 순우리말 이름 후보 어휘 추출을 위한 텍스트 데이터화  
**도구**: EasyOCR (한국어), PyMuPDF, Python 3.x  

---

## 디렉터리 구조

```
SKN29-3rd-4Team/
├── docs/
│   └── ocr_pipeline.md          ← 이 문서
├── src/data/
│   ├── ocr_extract.py           ← 1단계: PDF → raw OCR
│   ├── ocr_clean.py             ← 2단계: raw → 정제
│   └── extract_compare_pages.py ← 보조: 비교용 페이지 이미지 추출
└── data/processed/ocr/
    ├── ocr_raw_full.txt         ← EasyOCR 원본 출력 (Git LFS)
    ├── ocr_cleaned.txt          ← 정제 완료 텍스트 (Git LFS)
    ├── ocr_clean_log.txt        ← 정제 변경 로그
    ├── ocr_log.txt              ← 추출 실행 로그
    ├── ocr_progress.json        ← 페이지별 진행 상태 (재개용)
    └── compare/                 ← 원본 PDF vs OCR 비교용 이미지
        ├── page_8.png
        ├── page_9.png
        ├── page_50.png
        ├── page_100.png
        ├── page_200.png
        └── page_340.png
```

---

## 환경 설정

```bash
pip install easyocr pymupdf pillow numpy
```

**EasyOCR 모델 파일** (첫 실행 시 자동 다운로드, `~/.EasyOCR/model/`):
- `craft_mlt_25k.pth` (83MB) — 텍스트 영역 감지
- `korean_g2.pth` (16MB) — 한국어 문자 인식

**알려진 환경 오류 및 해결책**:

| 오류 | 원인 | 해결 |
|------|------|------|
| OMP Error #15 (libiomp5md.dll) | PyTorch + EasyOCR OpenMP 이중 초기화 | `os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"` |
| UnicodeEncodeError cp949 | EasyOCR 다운로드 진행바 UTF-8 문자 출력 | `sys.stdout.reconfigure(encoding="utf-8")` + `$env:PYTHONIOENCODING="utf-8"` |

---

## 1단계: PDF → raw OCR (`ocr_extract.py`)

### PDF 페이지 구분

| 페이지 인덱스 | 내용 | 처리 방식 |
|-------------|------|----------|
| idx 0~2 (p1~3) | 표지, 인가도장 | 건너뜀 |
| idx 3~335 (p4~336) | 본문 — 단일 컬럼 | 단일 OCR |
| idx 336~338 (p337~339) | 섹션 구분, 빈 면 | 건너뜀 |
| idx 339~405 (p340~406) | 색인 — 2단 컬럼 | 좌/우 분리 OCR |

### 처리 흐름

```
PDF 페이지
  └─ get_pixmap(dpi=200) → PNG → numpy array (RGB)
       ├─ [본문] readtext(detail=0, paragraph=True)
       └─ [색인] 이미지 w//2 기준 좌/우 분리
                  ├─ 좌절반 → readtext()
                  └─ 우절반 → readtext()
                             ↓ 좌+우 순서로 합산
```

### 출력 형식

```
=== PAGE 4 [본문] ===
가납사니 [명] 쓸모없는 말이나 행동을 하는 사람...

=== PAGE 340 [색인] ===
가납사니 명 4
...
```

### 중단 후 재개

`ocr_progress.json`에 완료된 페이지 인덱스를 저장하므로, 실행 중단 후 재실행 시 이어서 처리.

---

## 2단계: OCR 정제 (`ocr_clean.py`)

**입력**: `ocr_raw_full.txt`  
**출력**: `ocr_cleaned.txt`, `ocr_clean_log.txt`

### 적용된 정제 규칙 (총 9,932건 수정)

| # | 규칙 | 수정 건수 | 예시 |
|---|------|----------|------|
| 1 | 품사 태그 정규화 | 5,443건 | `명]`, `[명_`, `명!` → `[명]` |
| 2 | 페이지 헤더 제거 | 405건 | `아름답고 정겨운 우리말 12` → 삭제 |
| 3 | 놓다 계열 오류 | 100건 | `농고→놓고`, `농다→놓다` (17 패턴) |
| 4 | 조사 수정 | 3,356건 | `올→을`, `흘→을` (받침 있는 음절 뒤) |
| 5 | 었다/없다 혼동 | 279건 | `먹없다→먹었다` (동사어간+없다) |
| 6 | 예문 마커 정규화 | 349건 | `끼`, `9 한글` → `¶` |

### 조사 수정 적용 범위

보수적 접근 원칙: **false positive 위험이 높은 패턴은 의도적으로 제외**

**적용 O**:
- `올` → `을`: 받침 있는 음절 바로 뒤 + 경계 문자 앞
- `흘` → `을`: 동일 조건

**적용 X (제외 이유)**:
- `틀/름/률` → `를`: `이름`, `아름`, `틀리다` 오탈자 발생 위험
- `논/눈` → `는`: `논밭`, `눈(目)` 등 내용어 손상 위험

---

## 의도적 미수정 항목 (잔존 오류)

| 유형 | 건수 | 설명 |
|------|------|------|
| `틀/름/률` 조사 오인식 | 389줄 | `를` 자리에 `틀/름/률` 등 잔존 |
| `논/눈` 조사 오인식 | 114줄 | `는` 자리에 `논/눈` 잔존 |
| 한자 OCR 실패 (`#`/`@`) | 399줄 | 한자 인식 불가 → 기호 잔재 |
| 비정상 품사 태그 | 38줄 | `텅`, `탑`, `봄` 등 패턴 불명확 |

> 위 항목들은 자동 정제 시 내용어(이름·단어) 손상 위험이 있어 수동 검토 대상으로 남겨둠.

---

## 실행 방법

```bash
# 1단계: OCR 추출 (약 2~3시간 소요, CPU 기준)
python src/data/ocr_extract.py

# 2단계: 정제
python src/data/ocr_clean.py
```

---

## 주요 OCR 오류 패턴 참고

| 오류 유형 | 원인 |
|---------|------|
| ㅡ↔ㅗ 모음 혼동 | `을→올`, `를→롤` |
| ㄹ↔ㅌ 혼동 | `를→틀` |
| ㅎ 받침 손실 | `놓→농` |
| 었다↔없다 | 어간 뒤 `없다` 오인식 |
| 예문 마커 | `¶` → `끼`, `9`, `기`, `%`, `7` 등 |
| 한자 | `#`, `@` 등 garbage 문자 |
