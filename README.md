# SKN29-3rd-4Team — 조건 기반 맞춤 작명 QA 시스템

> SK네트웍스 Family AI 캠프 29기 | 3차 단위 프로젝트

---


## 팀 소개

> 캐릭터 이미지 파일: `docs/assets/char_임준.png` / `char_최지용.png` / `char_윤대성.png` / `char_이지현.png`

<table>
  <thead>
    <tr>
      <th align="center">구분</th>
      <th align="center">임준</th>
      <th align="center">최지용</th>
      <th align="center">윤대성</th>
      <th align="center">이지현</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td align="center"><b>캐릭터</b></td>
      <td align="center"><img src="docs/assets/char_임준.png" width="150"/></td>
      <td align="center"><img src="docs/assets/char_최지용.png" width="150"/></td>
      <td align="center"><img src="docs/assets/char_윤대성.png" width="150"/></td>
      <td align="center"><img src="docs/assets/char_이지현.png" width="150"/></td>
    </tr>
    <tr>
      <td align="center"><b>역할</b></td>
      <td align="center">팀장<br/><sub>길드 마스터</sub></td>
      <td align="center">Graph DB<br/><sub>검증 조율사</sub></td>
      <td align="center">데이터<br/><sub>기록관</sub></td>
      <td align="center">RAG<br/><sub>사전 감정사</sub></td>
    </tr>
    <tr>
      <td align="center"><b>담당</b></td>
      <td>Git 브랜치·서버 인프라 관리<br/>전체 일정 조율 및 작업 방향 컨펌</td>
      <td>Neo4j 스키마 설계<br/>한자-오행 관계 인덱싱<br/>graph_server.py 구현</td>
      <td>Unihan 파싱·오행표 병합<br/>논문 PDF 전처리<br/>ChromaDB 인덱싱</td>
      <td>한자·어휘 데이터 검증<br/>임베딩 흐름 정리<br/>RAG 기반 QA 설계</td>
    </tr>
    <tr>
      <td align="center"><b>소개</b></td>
      <td>Git 흐름과 서버 인프라를 관리하며 팀의 작업이 한 방향으로 합쳐지도록 이끄는 길드 마스터</td>
      <td>Neo4j 그래프와 데이터 정합성을 검증하며 파이프라인이 안정적으로 연결되도록 조율하는 검증자</td>
      <td>흩어진 원천 자료와 문서를 연결해 프로젝트의 지식 기반을 여는 기록관</td>
      <td>한자와 어휘의 의미를 세밀하게 검토해 추천 결과의 신뢰도를 높이는 사전 감정사</td>
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
| 핵심 기술 | LangGraph · FastMCP · ChromaDB · Neo4j · OpenAI API |
| LLM | gpt-4o-mini (파인튜닝 예정) |
| 임베딩 | jhgan/ko-sroberta-multitask (로컬) |

---

## 주요 기능

- **조건 기반 이름 추천** — 오행·획수·뜻·발음·성씨 조건을 자연어로 입력, 81수리 4격 자동 계산
- **법령 적법성 검증** — 인명용 한자 규정 / 가족관계등록법 조항 근거 제시
- **학술 논문 기반 트렌드 분석** — 작명 관련 논문 266건(통계표 37건 포함) RAG 검색
- **출처 명시 답변** — 모든 답변에 근거 라벨 포함 `[한자: 자원오행표 木오행]` `[논문: 제목(연도)]` `[출처: law_col]`
- **ReAct 루프** — 복합 질의 처리 (다중 도구 순차 호출, 최대 3회)

---

## 시스템 아키텍처

```
사용자 자연어 입력
        ↓
[LangGraph StateGraph — ReAct Router]  (src/graph/naming_graph.py)
   llm_router → 도구 선택 → 결과 누적 → 반복(최대 3회) → generate
        ↓
┌──────────────────────────────────────────────────┐
│ internal_rag  : ChromaDB 벡터 검색               │
│   수리 / 오행 / 한자 / 법령 / 순우리말 / 논문    │
│                                                  │
│ graph_db      : Neo4j 한자-오행-관계 탐색        │
│                                                  │
│ sql_db        : 81수리 4격 계산 / 오행 조합 분석  │
│                                                  │
│ external_api  : 국가법령정보 API / 우리말샘 API  │
└──────────────────────────────────────────────────┘
        ↓
[MCP 서버 — FastMCP]  (src/mcp/)
rag_server · db_server · law_server · graph_server
        ↓
[LLM 답변 생성 — gpt-4o-mini]
조건 충족 근거 + 출처 라벨 포함 최종 답변
```

---

## ChromaDB 컬렉션 현황

| 컬렉션 | 건수 | 내용 |
|---|---|---|
| `hanja_col` | 2,420건 | 한자 뜻·음·획수·자원오행·발음오행 (원획법 기준) |
| `suri_col` | 81건 | 획수 합산 0~81 수리 운세 풀이 |
| `ohaeng_col` | 125건 | 오행 조합 125종 상생/상극 운세 |
| `law_col` | 248건 | 가족관계등록법 / 인명용 한자 규정 |
| `urimalsam_col` | 301건 | 순우리말 이름 (baby-name.kr 크롤링) |
| `paper_col` | 266건 | 작명 관련 학술 논문 (본문 229건 + 통계표 37건) |
| `trend_col` | 인덱싱 중 | 연도별 출생신고 이름 빈도 통계 |

> 검색 시 `hanja_col`은 획수·오행 조건 필터를 자동 적용하며, `paper_col`은 쿼리에 "표"/"통계" 포함 시 통계표 청크 우선 검색.

---

## MCP 서버 및 도구 목록

총 **4개 서버 · 16개 도구**

### `rag_server.py` — ChromaDB 벡터 검색 (2 tools)

| 도구 | 설명 |
|---|---|
| `search_rag` | 컬렉션 지정 의미 검색. hanja_col은 획수·오행 필터, paper_col은 chunk_type 필터 자동 적용 |
| `list_collections` | 컬렉션 목록 및 문서 수 조회 |

### `db_server.py` — 수리/오행 연산 (5 tools)

| 도구 | 설명 |
|---|---|
| `get_surname_strokes` | 성씨 원획법 한자 획수 조회 (환각 방지용) |
| `find_lucky_strokes` | 성씨 획수 기준 81수리 吉數 조합 역산 |
| `calculate_name_suri` | 이름 4격(원/형/이/정) 수리 계산 및 길흉 판정 |
| `lookup_ohaeng_combo` | 오행 3자 조합 상생/상극 흐름 분석 |
| `search_name_stats` | 2016~2026 출생신고 이름 빈도·순위 조회 |

### `law_server.py` — 국가법령 API (3 tools)

| 도구 | 설명 |
|---|---|
| `search_law` | 가족관계등록법 / 인명용 한자 규정 조항 검색 |
| `get_law_article` | 특정 조항 전문 조회 |
| `verify_korean_word` | 우리말샘 API 어휘 유효성 확인 |

### `graph_server.py` — Neo4j 그래프 탐색 (6 tools)

| 도구 | 설명 |
|---|---|
| `check_graph_status` | Neo4j 연결 상태 확인 |
| `lookup_hanja` | 한자 기본 정보 조회 |
| `check_person_name_hanja` | 인명용 한자 여부 검증 |
| `get_ohaeng_relations` | 오행 상생/상극 관계 탐색 |
| `recommend_hanja_by_ohaeng` | 오행 조건 기반 한자 추천 |
| `answer_graph_query` | 자연어 기반 Neo4j Cypher 쿼리 실행 |

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
| 작명 관련 학술 논문 | 논문 PDF 전처리 | PDF→JSON | 수집 완료 (266청크) |

> API 키: `OPENAI_API_KEY` · `LAW_API_KEY` · `URIMALSAM_API_KEY` 발급 완료

---

## 디렉토리 구조

```
SKN29-3rd-4Team/
├── data/
│   ├── raw/
│   │   ├── unihan/            # Unicode Unihan 원본 TXT
│   │   ├── pdf/               # 법령·논문 원본 PDF
│   │   └── reference/         # peoplehanja.json / 81suri.json / yinyang.json
│   │                          # johab.json / 2016_2026상위_출생신고_이름_현황.xls
│   ├── processed/             # 전처리 완료 JSON
│   │   ├── hanja_documents.json
│   │   ├── suri_documents.json
│   │   ├── ohaeng_documents.json
│   │   ├── law_articles.json
│   │   ├── urimalsam_names.json
│   │   └── unihan_mapping/
│   └── chroma/                # ChromaDB PersistentClient 저장소
├── src/
│   ├── graph/                 # LangGraph StateGraph (ReAct)
│   │   └── naming_graph.py
│   └── mcp/                   # FastMCP 서버 4종
│       ├── rag_server.py      # ChromaDB 검색 (2 tools)
│       ├── db_server.py       # 수리/오행 연산 (5 tools)
│       ├── law_server.py      # 국가법령 API (3 tools)
│       └── graph_server.py    # Neo4j 탐색 (6 tools)
├── docs/
│   ├── project_idea_naming.md
│   ├── 진행_체크리스트.md
│   ├── internal_rag_명세서.md
│   └── db_server_명세서.md
└── README.md
```

---

## 진행 현황

| 단계 | 내용 | 상태 |
|---|---|---|
| 1단계 | 데이터 수집 및 구조화 | ✅ 완료 |
| 2단계 | 전처리 (법령 PDF KoNLPy Okt 파싱) | ✅ 완료 |
| 3단계 | ChromaDB 인덱싱 (6컬렉션) | ✅ 완료 |
| 3단계 | Neo4j 스키마 설계 및 인덱싱 | 🔄 진행 중 |
| 4단계 | LangGraph StateGraph 기본 구조 + 4방향 Router | ✅ 완료 |
| 4단계 | `graph_db_node` → `graph_server.py` 연결 | ⬜ 예정 |
| 4단계 | ReAct 루프 (다중 의도 질의 처리) | ⬜ 예정 |
| 4단계 | 출처 라벨 + 면책 고지 답변 형식 | ⬜ 예정 |
| 5단계 | MCP 서버 4종 · 16개 도구 구현 | ✅ 완료 |
| 6단계 | LLM 답변 생성 (gpt-4o-mini) | 🔄 진행 중 |
| 7단계 | gpt-4o-mini 파인튜닝 (CoT QA 데이터셋) | ⬜ 예정 |
| 평가 | Ground Truth QA 30~50개 + LLM-as-a-Judge | ⬜ 예정 |

---


## 환경 설정

```bash
# 저장소 클론
git clone https://github.com/Somber-7/SKN29-3rd-4Team.git
cd SKN29-3rd-4Team

# conda 환경 생성 (Python 3.11)
conda create -n skn29-3rd python=3.11
conda activate skn29-3rd

# 패키지 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일에 아래 키 입력:
# OPENAI_API_KEY=...
# LAW_API_KEY=...
# URIMALSAM_API_KEY=...
```

---

> 최종 업데이트: 2026-06-14
