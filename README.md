# SKN29-3rd-4Team — 조건 기반 맞춤 작명 QA 시스템

> SK네트웍스 Family AI 캠프 29기 | 3차 단위 프로젝트

---


## 팀 소개

<table width="100%">
  <thead>
    <tr>
      <th width="12%" style="text-align: center; vertical-align: middle;">구분</th>
      <th width="22%" style="text-align: center; vertical-align: middle;">임준</th>
      <th width="22%" style="text-align: center; vertical-align: middle;">최지용</th>
      <th width="22%" style="text-align: center; vertical-align: middle;">윤대성</th>
      <th width="22%" style="text-align: center; vertical-align: middle;">이지현</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td align="center"><b>캐릭터</b></td>
      <td align="center"><img src="docs/assets/char_임준.png" style="width: 100%; max-width: 150px; height: auto; aspect-ratio: 1/1; object-fit: cover;" /></td>
      <td align="center"><img src="docs/assets/char_최지용.png" style="width: 100%; max-width: 150px; height: auto; aspect-ratio: 1/1; object-fit: cover;" /></td>
      <td align="center"><img src="docs/assets/char_윤대성.png" style="width: 100%; max-width: 150px; height: auto; aspect-ratio: 1/1; object-fit: cover;" /></td>
      <td align="center"><img src="docs/assets/char_이지현.png" style="width: 100%; max-width: 150px; height: auto; aspect-ratio: 1/1; object-fit: cover;" /></td>
    </tr>
    <tr>
      <td align="center"><b>담당</b></td>
      <td>Git 브랜치·서버 인프라 관리<br/>전체 일정 조율 및 작업 방향 컨펌</td>
      <td>Neo4j 스키마 설계<br/>한자-오행 관계 인덱싱<br/>graph_server.py 구현</td>
      <td>Unihan 파싱·오행표 병합<br/>논문 PDF 전처리<br/>ChromaDB 인덱싱</td>
      <td>한자·어휘 데이터 검증<br/>임베딩 흐름 정리<br/>RAG 기반 QA 설계</td>
    </tr>
  </tbody>
</table>

---

## 프로젝트 개요

성씨·오행·획수·뜻·발음 등의 조건을 자연어로 입력하면,
**ChromaDB(RAG) · Neo4j(그래프) · 국가법령 API** 세 경로를 통해
조건을 충족하는 한자 또는 순우리말 이름을 추천하고,
가족관계등록법·인명용 한자 규정 근거를 출처 라벨과 함께 제공하는 대화형 QA 시스템.

| 항목 | 내용 |
|---|---|
| 대주제 | LLM을 연동한 내외부 문서 기반 질의응답 시스템 |
| 도메인 | 작명 / 법령 (인명용 한자 규정, 가족관계등록법) |
| 핵심 기술 | LangGraph · ChromaDB · Neo4j · OpenAI API |
| 운영 기본 모델 | gpt-5.4-mini |
| 실험 트랙 모델 | Qwen3.5-4B LoRA 파인튜닝 |
| 임베딩 | jhgan/ko-sroberta-multitask (로컬) |

---

## 주요 기능

- **조건 기반 이름 추천** — 오행·획수·뜻·발음·성씨 조건을 자연어로 입력, 81수리 4격 자동 계산
- **법령 적법성 검증** — 인명용 한자 규정 / 가족관계등록법 조항 근거 제시
- **학술 논문 기반 트렌드 분석** — 작명 관련 논문 264건(통계표 48건 포함) RAG 검색
- **출처 명시 답변** — 모든 답변에 근거 라벨 포함 `[한자: 자원오행표 木오행]` `[논문: 제목(연도)]` `[출처: law_col]`
- **ReAct 루프** — 복합 질의 처리 (다중 도구 순차 호출, 최대 5회)

---

## 시스템 아키텍처

```text
사용자 자연어 입력 (Open WebUI)
        ↓
[Pipeline Server :9099]  (pipelines/naming_pipeline.py)
        ↓
[LangGraph StateGraph — ReAct Router]  (src/graph/naming_graph.py)
   llm_router → 도구 선택 → 결과 누적 → 반복(최대 5회) → generate
        ↓
[ MCP Tools ]
 ├─ RAG Tool (rag_server.py)
 │   └─ ChromaDB (한자/법령/오행/수리/순우리말/논문) 검색
 │
 ├─ DB Tool (db_server.py)
 │   └─ 81수리 4격 계산 / 吉수 역산 / 오행 조합 분석
 │
 ├─ Graph Tool (graph_server.py)
 │   └─ Neo4j 한자-오행-법령 구조 탐색
 │
 └─ Law API Tool (law_server.py)
     └─ 국가법령정보 API / 우리말샘 API 연동
        ↓
[LLM 답변 생성 — gpt-5.4-mini]
조건 충족 근거가 포함된 최종 답변을 WebUI로 반환
```

---

## ChromaDB 컬렉션 현황

| 컬렉션 | 건수 | 내용 |
|---|---|---|
| `hanja_col` | 2,438건 | 한자 뜻·음·획수·자원오행·발음오행 (성씨 보조 포함) |
| `suri_col` | 81건 | 획수 합산 0~81 수리 운세 풀이 |
| `ohaeng_col` | 125건 | 오행 조합 125종 상생/상극 운세 |
| `law_col` | 248건 | 가족관계등록법 / 인명용 한자 규정 |
| `urimalsam_col` | 301건 | 순우리말 이름 (baby-name.kr 크롤링) |
| `paper_col` | 264건 | 작명 관련 학술 논문 (본문 216건 + 통계표 48건) |

> 검색 시 `hanja_col`은 획수·오행 조건 필터를 자동 적용하며, `paper_col`은 쿼리에 "표"/"통계" 포함 시 통계표 청크 우선 검색.

---

## MCP 도구 모듈 목록

총 **4개 그룹 · 16개 도구**

### `rag_server.py` — ChromaDB 벡터 검색 모듈 (2 tools)

| 도구 | 설명 |
|---|---|
| `search_rag` | 컬렉션 지정 의미 검색. hanja_col은 획수·오행 필터, paper_col은 chunk_type 필터 자동 적용 |
| `list_collections` | 컬렉션 목록 및 문서 수 조회 |

### `db_server.py` — 수리/오행 연산 모듈 (5 tools)

| 도구 | 설명 |
|---|---|
| `get_surname_strokes` | 성씨 원획법 한자 획수 조회 (환각 방지용) |
| `find_lucky_strokes` | 성씨 획수 기준 81수리 吉數 조합 역산 |
| `calculate_name_suri` | 이름 4격(원/형/이/정) 수리 계산 및 길흉 판정 |
| `lookup_ohaeng_combo` | 오행 3자 조합 상생/상극 흐름 분석 |
| `search_name_stats` | 2016~2026 출생신고 이름 빈도·순위 조회 |

### `law_server.py` — 국가법령 API 모듈 (3 tools)

| 도구 | 설명 |
|---|---|
| `search_law` | 가족관계등록법 / 인명용 한자 규정 조항 검색 |
| `get_law_article` | 특정 조항 전문 조회 |
| `verify_korean_word` | 우리말샘 API 어휘 유효성 확인 |

### `graph_server.py` — Neo4j 그래프 탐색 모듈 (6 tools)

| 도구 | 설명 |
|---|---|
| `check_graph_status` | Neo4j 연결 상태 확인 |
| `lookup_hanja` | 한자 기본 정보 조회 |
| `check_person_name_hanja` | 인명용 한자 여부 검증 |
| `get_ohaeng_relations` | 오행 상생/상극 관계 탐색 |
| `recommend_hanja_by_ohaeng` | 오행 조건 기반 한자 추천 |
| `answer_graph_query` | 자연어 기반 Neo4j Cypher 쿼리 실행 |

---

## 🏆 모델 평가 및 비교 결론

LLM의 단순 생성이 아닌 작명이라는 특수 도메인의 **조건 충족(수리, 오행, 법령 등)**을 위해 두 가지 트랙으로 평가를 진행했습니다.

### 1. GPT-5.4-mini 운영 Pipeline (기본 모델)
- **방식**: RAG + Tool + LangGraph 기반 복합 추론
- **평가 결과 (11개 케이스)**: **성공 처리 11/11, 전체 평균 4.09 / 5점**
- **강점**: 81수리 계산, 순우리말 검증, 법령 근거 제시 등에서 매우 높은 정확도(Groundedness)와 조건 충족도를 보임. 운영 파이프라인으로 매우 적합함.

### 2. Qwen3.5-4B LoRA 파인튜닝 (실험 트랙)
- **방식**: 파인튜닝 모델 단독 응답 (RAG 미사용)
- **평가 결과 (11개 케이스)**: **성공 처리 10/11, 전체 평균 1.63 / 5점**
- **한계점**: 챗봇으로서의 말투와 응답 형식 모방에는 성공했으나, **수리 계산 오류, 오행 상생/상극 환각(Hallucination), 법령 팩트체크 실패** 등 4B 소형 모델 체급의 한계를 보임.

### 💡 최종 결론
작명 도메인은 단순 문장 생성보다 **사실성(Factuality)과 계산 정확성**이 중요합니다. 따라서 **GPT Pipeline을 최종 운영 모델로 확정**하였으며, Qwen 파인튜닝은 향후 Hybrid 구조에서 단순 응답을 보조하는 경량 모델로서의 가능성을 확인하는 실험적 성과로 남깁니다.

---

## 데이터 소스

| 데이터 | 출처 | 형태 | 상태 |
|---|---|---|---|
| Unicode Unihan DB (획수/뜻/음) | unicode.org | TXT | 수집 완료 |
| 자원오행·발음오행 분류표 (~2,400자) | 직접 구조화 | XLSX | 수집 완료 |
| 인명용 한자 4,975자 (peoplehanja.json) | 직접 구조화 | JSON | 수집 완료 |
| 가족관계등록법 / 인명용 한자 규정 PDF | 국가법령정보 | PDF | 수집 완료 |
| 순우리말 이름 (baby-name.kr 1~11p) | 크롤링 | JSON | 수집 완료 (301건) |
| 81수리 운세 / 오행 조합 운세 | 직접 구조화 | JSON | 수집 완료 |
| 출생신고 이름 빈도 통계 (2016~2026) | 법원행정처 공공데이터 | XLS | 수집 완료 |
| 작명 관련 학술 논문 | 논문 PDF 전처리 | PDF→JSON | 수집 완료 (264청크) |
| 한자 확장 후보군 6,564건 | hanja.pdf 원본 + Unihan 교차검증 | JSON | 운영 DB 대기 상태 |

> API 키: `OPENAI_API_KEY` · `LAW_API_KEY` · `URIMALSAM_API_KEY` 발급 완료

---

## 디렉토리 구조

```
SKN29-3rd-4Team/
├── data/
│   ├── raw/                   # Unihan, 법령 PDF, 81수리 등 원천 자료
│   ├── processed/             # 전처리 완료 JSON 메인 데이터 및 확장 후보군 데이터
│   └── chroma/                # ChromaDB PersistentClient 저장소
├── src/
│   ├── graph/                 # LangGraph StateGraph (naming_graph.py)
│   ├── mcp/                   # FastMCP 서버 4종 (rag, db, law, graph)
│   ├── data/                  # 데이터 수집 및 인덱싱 스크립트
│   └── preprocess/            # 한자/논문 전처리 보조 스크립트
├── docker/                    # Pipeline Dockerfile 정의
├── pipelines/                 # Open WebUI 연결 진입점 (naming_pipeline.py)
├── finetuning/                # Qwen3.5-4B LoRA 학습, 데이터 생성, API 서빙 스크립트
├── tests/                     # RAG 평가(rag_eval), Qwen 평가 스크립트 모음
├── docs/                      # 프로젝트 설명 및 산출물 문서
├── docker-compose.yml         # Pipeline Server 실행 구성
└── README.md                  # 프로젝트 통합 설명서
```

---

## 진행 현황

| 단계 | 내용 | 상태 |
|---|---|---|
| 1단계 | 데이터 수집 및 구조화 | ✅ 완료 |
| 2단계 | 전처리 (법령 PDF KoNLPy Okt 파싱) | ✅ 완료 |
| 3단계 | ChromaDB 인덱싱 (6컬렉션) | ✅ 완료 |
| 3단계 | Neo4j 스키마 설계 및 인덱싱 | ✅ 완료 |
| 4단계 | LangGraph StateGraph 설계 및 4방향 Router | ✅ 완료 |
| 4단계 | ReAct 루프 (다중 의도 질의 처리) 및 면책 고지 | ✅ 완료 |
| 5단계 | MCP 도구 모듈 4종 · 16개 도구 구현 | ✅ 완료 |
| 6단계 | LLM 답변 생성 (gpt-5.4-mini) | ✅ 완료 |
| 7단계 | Qwen3.5:4b QLoRA 파인튜닝 및 운영 모델 비교 평가 | ✅ 완료 |

---

## 환경 설정 및 시작 가이드

프로젝트 실행은 로컬 Conda 환경과 Docker 기반 환경 두 가지를 지원합니다.

### 1. Docker Compose로 Pipeline Server 실행 (권장)
```bash
# 저장소 클론
git clone https://github.com/Somber-7/SKN29-3rd-4Team.git
cd SKN29-3rd-4Team

# 환경변수 설정
cp .env.example .env
# .env 파일에 OPENAI_API_KEY, LAW_API_KEY, URIMALSAM_API_KEY 입력

# Pipeline Server 구동
docker-compose up -d
```
> 구동 후 Open WebUI 컨테이너를 연결하여 `http://localhost:9099`를 파이프라인 엔드포인트로 사용합니다.

### 2. 로컬 개발 환경 (Conda)
```bash
# conda 환경 생성 (Python 3.11)
conda create -n skn29-3rd python=3.11
conda activate skn29-3rd

# 패키지 설치
pip install -r requirements.txt
```

---

> 최종 업데이트: 2026-06-17
