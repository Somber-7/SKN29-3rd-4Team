# 프로젝트 아이디어: 조건 기반 맞춤 작명 QA 시스템

## 프로젝트 개요

| 항목 | 내용 |
|---|---|
| 대주제 | LLM을 연동한 내외부 문서 기반 질의응답 시스템 |
| 도메인 | 작명 / 법령 (인명용 한자 규정, 가족관계등록법) |
| 형태 | 팀 프로젝트 |
| 기간 | 2주 |
| 수업 기반 | Day 38~57 (8~12주차) |

---

## 문제 정의

사용자가 이름을 지을 때 다음 어려움을 겪음:
- 원하는 조건(오행, 뜻, 획수, 발음 등)을 충족하는 한자를 직접 찾기 어려움
- 추천받은 이름이 실제 출생신고(법적 등록) 가능한지 확인이 번거로움
- 작명소마다 기준이 달라 근거 없는 추천이 많음

→ **내부 문서(한자 속성DB, 오행 분류표, 우리말 이름 사전)** + **외부 문서(인명용 한자 규정, 가족관계등록법, 우리말샘 API)** 를 통합하여 조건 충족 근거와 법적 등록 가능 여부를 함께 제공하는 QA 시스템

---

## 시나리오

> "목(木) 오행이고 뜻이 밝음과 관련된 한자 이름 추천해줘" (rag — 오행/뜻 조건 검색)
> "준서라는 이름 출생신고 가능한가요?" (graph — 인명용 한자 규정 검증)
> "김씨 성에 어울리는 순우리말 이름 추천해줘" (rag — 우리말 이름 사전 검색)
> "이 한자가 인명용으로 허용되나요?" (external_api — 가족관계등록법 조문 근거)
> "획수 합이 16획인 이름 조합 찾아줘" (sql — 획수 수치 조건 필터)

---

## 수집 범위

| 항목 | 내용 |
|---|---|
| 내부 한자 데이터 | Unicode Unihan DB (획수/뜻/음) + 자원오행 분류표 (약 2400자) |
| 내부 이름 사전 | 국립국어원 우리말 이름 자료집 PDF |
| 외부 법령 | 국가법령정보 API → 가족관계등록법, 인명용 한자 규정 |
| 외부 어휘 | 우리말샘 오픈 API → 순우리말 뜻 검증 |
| 외부 통계 | 법원행정처 이름 통계 API → 추천 이름 빈도/희소성 |
| 폴백 | 미수집 법령 질문 시 국가법령정보 API 실시간 조회 → ChromaDB 캐싱 저장 |

### 데이터 수집 방법 상세

| 데이터 | 수집 방법 | 형태 | 상태 |
|---|---|---|---|
| Unicode Unihan DB (획수/뜻/음) | 직접 다운로드 (공개 데이터) | TXT | 수집 완료 |
| 자원오행 발음오행 분류표 | 엑셀 직접 구조화 (약 2400자) | XLSX | 수집 완료 |
| 국립국어원 우리말 이름 자료집 | 공식 홈페이지 PDF 직접 다운로드 | PDF | 수집 필요 |
| 가족관계등록법 / 인명용 한자 규정 | 국가법령정보 API (텍스트 직접 수신) | XML/JSON | API 키 발급 필요 |
| 우리말샘 어휘 정보 | 우리말샘 오픈 API (텍스트 직접 수신) | JSON | API 키 발급 필요 |
| 이름 빈도 통계 | 법원행정처 공공데이터 API | JSON | API 키 발급 필요 |

---

## 아키텍처

```
[1단계 — 데이터 수집 및 구조화]
① 내부: Unicode Unihan TXT 파싱 + 자원오행 XLSX 병합
         → 한자별 (음/뜻/획수/오행) 통합 JSON 구성
② 내부: 국립국어원 우리말 이름 PDF → pdfplumber 파싱
③ 외부: 국가법령정보 API → 가족관계등록법, 인명용 한자 규정 텍스트 수신
    ↓
[2단계 — 전처리]
KoNLPy (Okt) 형태소 분석 → 불용어 제거 → 텍스트 정규화
    ↓
[3단계 — 인덱싱]
청킹 전략 비교 실험 (Fixed-size / Recursive / Semantic)
    ├─ ChromaDB (Vector DB — 한자 속성, 우리말 이름, 법령 조문 검색)
    └─ Neo4j    (Graph DB — 한자-오행-법령 관계)
한자 수치 데이터 (획수, 오행코드) → SQLite 저장
    ↓
[4단계 — 질의 처리: LangGraph StateGraph]
Router 노드: 질문 분류
    ├─ internal_rag : ChromaDB 검색 → 조건 충족 한자/이름 + 출처 포함 답변
    ├─ graph        : Neo4j 탐색 → 인명용 한자 규정 적합성 검증
    ├─ sql          : SQLite 조회 → 획수/오행 수치 조건 필터
    └─ external_api : 미수집 법령 → 국가법령정보 API 실시간 조회
                      + 우리말샘 API 어휘 검증
                      → 결과 ChromaDB 캐싱 저장
    ↓
[5단계 — MCP 서버]
rag_server.py   : ChromaDB 한자/이름/법령 검색 Tool
graph_server.py : Neo4j 인명용 한자 규정 검증 Tool
db_server.py    : SQLite 획수/오행 필터 Tool
law_server.py   : 국가법령정보 API + 우리말샘 API Tool
    ↓
[6단계 — LLM 답변 생성]
OpenAI API + 출처 라벨 포함
[한자: 자원오행표 木오행] [법령: 가족관계등록법 제44조] [통계: 빈도 하위 10%]
    ↓
[7단계 — sLLM 파인튜닝 (병렬 진행)]
작명 조건 QA 데이터셋 구성 → QLoRA 파인튜닝 (EXAONE-3.5-2.4B 등)
RAG 파이프라인 완성 후 전담 담당자가 병렬로 진행
```

---

## Graph DB 설계 (Neo4j)

```
노드
  Hanja    : 한자, 음, 뜻, 획수, 자원오행, 발음오행
  Law      : 법령명, 조문번호 (가족관계등록법, 인명용 한자 규정)
  Category : 오행 분류 (木火土金水), 뜻 이미지 (밝음/강함/지혜 등)
  Name     : 순우리말 이름, 뜻, 어감
  Chunk    : 텍스트 청크, 임베딩 벡터

관계
  (Hanja)-[BELONGS_TO]->(Category)      ← 한자-오행 분류
  (Hanja)-[PERMITTED_BY]->(Law)         ← 인명용 한자 규정 허용 여부 핵심
  (Law)-[HAS_ARTICLE]->(Chunk)
  (Name)-[HAS_SOUND]->(Category)        ← 발음 오행 연결
```

활용 시나리오: "이 한자가 인명용으로 허용되는가", 오행별 한자 탐색, 법령 근거 제시

---

## 한국어 전처리 파이프라인

```
① 한자 데이터
   Unihan TXT 파싱 (획수/뜻/음 추출)
       ↓
   자원오행 XLSX 병합 → 한자별 통합 JSON
       ↓
   ChromaDB / Neo4j / SQLite 인덱싱

② 법령/이름 문서
   PDF 파싱 (pdfplumber) / API 텍스트 수신
       ↓
   KoNLPy (Okt) 형태소 분석
       ↓
   불용어 제거 + 특수문자 정규화
       ↓
   청킹 전략 비교 실험
       ├─ Fixed-size Chunking          (chunk_size=500)
       ├─ RecursiveCharacterTextSplitter
       └─ Semantic Chunking            (임베딩 유사도 기반)
       ↓
   ChromaDB / Neo4j 인덱싱
```

---

## 테스트 계획

| 평가 항목 | 방법 | 도구 |
|---|---|---|
| Ground Truth QA 셋 구성 | 조건별 이름 추천 + 법령 근거 질문-답변 쌍 작성 (30~50개) | 직접 구성 |
| Context Relevance | 검색된 청크가 질문 조건과 관련 있는가 | LLM-as-a-Judge (GPT) |
| Groundedness | 답변이 검색 문서(오행표/법령)에 근거하는가 (환각 여부) | LLM-as-a-Judge (GPT) |
| Answer Relevance | 추천 이름이 입력 조건을 충족하는가 | LLM-as-a-Judge (GPT) |
| 생성 품질 | 답변 품질 정량 평가 | BLEU, ROUGE |
| sLLM 벤치마크 | 파인튜닝 전후 성능 비교 | LM-Eval (lm-evaluation-harness) |

---

## 수업 연결 포인트

| 수업 내용 | 적용 위치 |
|---|---|
| Day 54 - RAG 3단계 파이프라인 | 전체 구조 |
| Day 54 - RecursiveCharacterTextSplitter | 법령/이름 문서 청킹 |
| Day 54 - 청킹 전략 비교 | Fixed-size / Recursive / Semantic 실험 |
| Day 55 - LangGraph Router 분기 | 질문 의도 분류 (4방향) |
| Day 55 - 기업문서 VectorDB 인덱싱 | 한자/법령 ChromaDB 저장 |
| Day 55 - 출처 포함 답변 (source_items) | 오행표 + 법령 출처 라벨 |
| Day 56 - FastMCP RAG 서버 | 검색 Tool 분리 |
| Day 56 - FastMCP DB 서버 | SQLite Tool 분리 |
| Day 52 - 복합의도 LLM 라우터 | 질문 분류 |

---

## 팀 역할 분담 (예시)

| 역할 | 담당 업무 |
|---|---|
| 데이터 담당 | Unihan 파싱 + 오행 XLSX 병합, 우리말 이름 PDF 파싱, KoNLPy 전처리, ChromaDB 인덱싱 |
| Graph DB 담당 | Neo4j 스키마 설계, 한자-오행-법령 관계 인덱싱, graph_server.py |
| 백엔드 담당 | LangGraph StateGraph 설계, Router 구현 (4방향), 폴백 캐싱 로직 |
| MCP 담당 | FastMCP 서버 구현 (rag / graph / db / law) |
| LLM/평가 담당 | 프롬프트 설계, 출처 포함 답변, LLM-as-a-Judge 평가, sLLM 파인튜닝 (병렬) |

---

## 2주 구현 로드맵

```
1주차
├─ Day 1  : Unihan TXT 파싱 + 자원오행 XLSX 병합 → 한자 통합 JSON 구성
│           국가법령정보 API 키 발급 + 인명용 한자 규정 수집
├─ Day 2  : 우리말 이름 PDF 파싱 + KoNLPy 전처리 + 청킹 전략 비교 실험 (3종)
├─ Day 3  : ChromaDB 인덱싱 + Neo4j 스키마 설계 (한자-오행-법령 관계)
├─ Day 4  : LangGraph StateGraph 기본 구조 설계 (4방향 Router)
└─ Day 5  : FastMCP 서버 구현 (rag / graph / db / law)

2주차
├─ Day 1  : Router 분기 완성 + 외부 API 폴백 + ChromaDB 캐싱 로직
├─ Day 2  : 프롬프트 최적화 + 조건 충족 근거 + 출처 포함 답변 완성
├─ Day 3  : Ground Truth QA 셋 구성 + LLM-as-a-Judge 평가 파이프라인
│           (병렬) sLLM 파인튜닝 데이터셋 구성 + QLoRA 학습 시작
├─ Day 4  : BLEU/ROUGE 측정 + LM-Eval 벤치마크 + 테스트 결과 보고서 작성
└─ Day 5  : 통합 테스트 + 발표 시연 시나리오 구성
```

> **주의**: Unihan 파싱 + 오행 병합(Day 1)이 완료되지 않으면 이후 인덱싱 전체가 지연됨.
> Day 1 완료 기준: 한자별 (음/뜻/획수/오행) 통합 JSON 로컬 저장 확인.

---

## 타 아이디어 대비 차별점

| 항목 | 작명 QA | 금융 공시 (DART) | 노무/인사 QA |
|---|---|---|---|
| 핵심 차별화 | 조건 기반 이름 추천 + **법령 적법성 검증** | 기업-공시 관계 탐색 | 내규 vs 법령 비교 |
| RAG 근거 | 오행표 + 인명용 한자 규정 | 사업보고서 본문 | 취업규칙 + 노동법령 |
| Graph DB 활용 | 한자-오행-법령 연결 | 기업-보고서-섹션 | 법령-조항-내규 |
| 데이터 사전 준비 | Unihan + 오행표 수집 완료 | DART API 즉시 발급 | 법령 API 즉시 발급 |
| 발표 차별화 | 독창적 도메인 | 실제 금융 데이터 | 청중 공감도 높음 |
| 대주제 부합도 | 법령 문서 추가로 RAG 성격 강화 | 매우 높음 | 매우 높음 |

## 제약 및 리스크

| 항목 | 내용 | 대응 |
|---|---|---|
| 오행 유파 기준 상이 | 유파마다 오행 분류가 다름 | 자원오행 1가지 기준으로 고정 후 명시 |
| 법적 등록 가능 여부 | 추천 이름의 출생신고 가능 여부 완전 보장 불가 | 인명용 한자 규정 근거 제시 + 면책 고지 |
| 우리말 이름 PDF 수집 | 국립국어원 PDF 파싱 품질 편차 가능 | pdfplumber 적용, 스캔본 시 OCR 추가 검토 |
| 동음이의 한자 조합 폭발 | 같은 음의 한자 다수 시 조합 수 급증 | 인명용 한자 + 빈도 상위 필터링으로 제한 |
