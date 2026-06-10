# SKN29-3rd-4Team — 조건 기반 맞춤 작명 QA 시스템

> SK네트웍스 Family AI 캠프 29기 | 3차 단위 프로젝트

---

## 프로젝트 개요

사용자가 원하는 조건(오행, 뜻, 발음, 획수, 성씨 등)을 자연어로 입력하면,  
내부 문서 기반 RAG로 조건을 충족하는 한자 이름 또는 순우리말 이름을 추천하고,  
**인명용 한자 규정 · 가족관계등록법** 근거를 함께 제공하는 대화형 QA 시스템.

| 항목 | 내용 |
|---|---|
| 대주제 | LLM을 연동한 내외부 문서 기반 질의응답 시스템 |
| 도메인 | 작명 / 법령 (인명용 한자 규정, 가족관계등록법) |
| 기간 | 2주 |
| 핵심 기술 | RAG · LangGraph · MCP · ChromaDB · Neo4j |

---

## 주요 기능

- **조건 기반 이름 추천** — 오행, 획수, 뜻, 발음, 성씨 조건을 자연어로 입력
- **법령 적법성 검증** — 인명용 한자 규정 / 가족관계등록법 근거 제시
- **한자 · 순우리말 병렬 검색** — 두 경로를 동시에 탐색 후 통합 추천
- **출처 명시** — 모든 답변에 근거 문서 라벨 포함 `[오행표: 木오행]` `[법령: 가족관계등록법 제44조]`
- **대화형 QA** — 추가 질문 루프 지원

---

## 시스템 아키텍처

```
사용자 자연어 입력
        ↓
[LangGraph StateGraph — Router]
        ↓
┌───────────────────────────────────────┐
│ internal_rag  : ChromaDB 검색         │
│                한자속성 / 법령 / 이름  │
│                                       │
│ graph         : Neo4j 탐색            │
│                한자-오행-법령 관계     │
│                                       │
│ sql           : SQLite 조회           │
│                획수 / 오행 수치 필터   │
│                                       │
│ external_api  : 국가법령정보 API       │
│                우리말샘 API 폴백       │
└───────────────────────────────────────┘
        ↓
[MCP 서버 — FastMCP]
rag_server / graph_server / db_server / law_server
        ↓
[LLM 답변 생성 — OpenAI API]
조건 충족 근거 + 출처 라벨 포함
```

---

## 데이터 소스

| 데이터 | 출처 | 형태 | 상태 |
|---|---|---|---|
| Unicode Unihan DB (획수/뜻/음) | unicode.org | TXT | 수집 완료 |
| 자원오행 · 발음오행 분류표 | 직접 구조화 (약 2400자) | XLSX | 수집 완료 |
| 국립국어원 우리말 이름 자료집 | 국립국어원 공식 PDF | PDF | 수집 필요 |
| 가족관계등록법 / 인명용 한자 규정 | 국가법령정보 API | XML/JSON | API 키 발급 필요 |
| 우리말샘 어휘 정보 | 우리말샘 오픈 API | JSON | API 키 발급 필요 |
| 이름 빈도 통계 | 법원행정처 공공데이터 API | JSON | API 키 발급 필요 |

---

## 디렉토리 구조

```
SKN29-3rd-4Team/
├── data/
│   ├── raw/
│   │   ├── unihan/          # Unicode Unihan 원본 TXT
│   │   ├── ohaeng/          # 자원오행 발음오행구분표.xlsx
│   │   ├── reference/       # 작명 판단 기준 참조 데이터
│   │   │                    #   peoplehanja.json  — 인명용 한자 4,975자 (한자·오행·획수·뜻·음)
│   │   │                    #   81suri.json       — 획수 합 0~81 수리 풀이
│   │   │                    #   yinyang.json      — 오행 조합 125종 운세 설명
│   │   │                    #   johab.json        — 초성별 한글 음절 목록
│   │   │                    #   2016_2026상위_출생신고_이름_현황.xls — 연도별 인기 이름 순위
│   │   ├── urimalsaem/      # 우리말샘 어휘 JSON (1.7GB)
│   │   └── pdf/             # 법령·문서 PDF
│   │                        #   hanja.pdf         — 인명용 한자표 (이미지 기반, OCR 필요)
│   │                        #   한글 글자 유니코드.pdf — 2018 인명용 한자 개정 근거 문서 (204p)
│   ├── processed/           # 전처리 완료 데이터 (JSON/CSV)
│   └── vector_db/           # ChromaDB 로컬 저장소
├── src/
│   ├── data/                # 수집 및 전처리 스크립트
│   ├── graph/               # Neo4j 인덱싱 스크립트
│   ├── mcp/                 # FastMCP 서버 (rag / graph / db / law)
│   ├── agent/               # LangGraph StateGraph, Router
│   └── llm/                 # 프롬프트 설계, 파인튜닝
├── tests/                   # 테스트 코드
├── notebooks/               # 탐색적 분석, 실험
└── docs/
    ├── project_idea_naming.md   # 아이디어 상세 기획서
    └── SKN29기_LLM_평가계획서.pdf
```

---

## 2주 구현 로드맵

| 주차 | 일자 | 작업 |
|---|---|---|
| 1주차 | Day 1 | Unihan 파싱 + 오행표 병합 → 한자 통합 JSON / 국가법령 API 연동 |
| | Day 2 | 우리말 이름 PDF 파싱 + KoNLPy 전처리 + 청킹 전략 비교 실험 |
| | Day 3 | ChromaDB 인덱싱 + Neo4j 스키마 설계 및 인덱싱 |
| | Day 4 | LangGraph StateGraph 기본 구조 설계 (4방향 Router) |
| | Day 5 | FastMCP 서버 구현 (rag / graph / db / law) |
| 2주차 | Day 1 | Router 분기 완성 + 외부 API 폴백 + ChromaDB 캐싱 로직 |
| | Day 2 | 프롬프트 최적화 + 출처 포함 답변 완성 |
| | Day 3 | Ground Truth QA 셋 구성 + LLM-as-a-Judge 평가 파이프라인 |
| | Day 4 | BLEU/ROUGE + LM-Eval 벤치마크 + 테스트 결과 보고서 |
| | Day 5 | 통합 테스트 + 발표 시연 시나리오 구성 |

---

## 팀 역할 분담

| 역할 | 담당 업무 |
|---|---|
| 데이터 담당 | Unihan 파싱 + 오행표 병합, 우리말 이름 PDF 파싱, KoNLPy 전처리, ChromaDB 인덱싱 |
| Graph DB 담당 | Neo4j 스키마 설계, 한자-오행-법령 관계 인덱싱, graph_server.py |
| 백엔드 담당 | LangGraph StateGraph 설계, Router 구현 (4방향), 폴백 캐싱 로직 |
| MCP 담당 | FastMCP 서버 구현 (rag / graph / db / law) |
| LLM/평가 담당 | 프롬프트 설계, 출처 포함 답변, LLM-as-a-Judge 평가, sLLM 파인튜닝 |

---

## 환경 설정

```bash
# 저장소 클론
git clone https://github.com/Somber-7/SKN29-3rd-4Team.git
cd SKN29-3rd-4Team

# conda 환경 생성 (Python 3.11)
conda create -n skn29-3rd python=3.11
conda activate skn29-3rd

# 패키지 설치 (추후 requirements.txt 추가 예정)
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일에 API 키 입력
```

---

> 본 README는 임시 문서입니다. 개발 진행에 따라 업데이트됩니다.
