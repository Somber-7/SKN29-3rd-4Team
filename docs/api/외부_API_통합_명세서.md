# 🌐 외부 API 통합 명세서

생성일: 2026년 6월 16일

이 문서는 프로젝트에서 사용하는 외부 API(국가법령정보 API, 우리말샘 API)의 사용 설명서 및 연동 가이드를 통합한 문서입니다.

---

## 1부. 국가법령정보 API

### 1.1 프로젝트 활용 프로세스 (2단계 아키텍처)

국가법령정보 API는 법령의 ID(일련번호)를 알아야 본문을 조회할 수 있으므로, 반드시 아래 **2단계 호출 순서**를 따라야 합니다.

1. **목록 조회 (Search)**: 법령명(예: "가족관계의 등록 등에 관한 규칙")으로 검색하여 `lsId`(법령일련번호)를 획득.
2. **본문 조회 (Get Body)**: 확보한 `lsId`로 법령 본문을 호출하여 실제 조항 텍스트 추출.

### 1.2 API 상세 명세

#### 📌 1단계: 현행법령 목록 조회 API

- **요청 URL**: `https://www.law.go.kr/DRF/lawSearch.do`

| **변수명** | **설정값** | **설명** |
| --- | --- | --- |
| **`OC`** | (인증키) | 국가법령정보센터 발급 ID |
| **`target`** | `law` | 검색 대상 (법령) |
| **`type`** | `JSON` | **[필수]** 결과 포맷 지정 |
| **`query`** | (검색어) | 법령명 (예: 가족관계의 등록 등에 관한 법률) |

#### 📌 2단계: 현행법령 본문 조회 API

- **요청 URL**: `https://www.law.go.kr/DRF/lawService.do`

| **변수명** | **설정값** | **설명** |
| --- | --- | --- |
| **`OC`** | (인증키) | 사용자 인증 ID |
| **`target`** | `law` | 검색 대상 |
| **`type`** | `JSON` | **[필수]** |
| **`lsId`** | (법령ID) | 1단계에서 획득한 `lsId` 값 |

### 1.3 출력 결과 (JSON) 및 추출 필드

| **데이터 계층** | **필드명** | **용도 및 프로젝트 활용 방안** |
| --- | --- | --- |
| **`Law`** | `BasicInfo` | 법령 명칭, 시행일(`efYd`) 등 기본 정보 확인 |
| **`Jo`** | `joNo` | **조문 번호**. (출처 라벨링: "제44조") |
|  | `joCts` | **조문 내용**. (RAG의 근거 문맥으로 LLM에 제공) |
| **`Byl`** | `bylSeq` | 별표 정보 (필요시 참고용 데이터) |

### 1.4 우리 프로젝트 적용 시나리오 (수정된 예시 코드)

`law_server.py`에서 법령 정보를 가져오는 핵심 로직입니다. JSON 계층 구조(KeyError 방지)와 정확한 법령 매칭 로직이 반영되었습니다.

```python
import requests

def get_law_content(law_name: str, article_num: str):
    try:
        # 1. 목록 조회로 lsId 획득
        search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={API_KEY}&target=law&type=JSON&query={law_name}"
        search_res = requests.get(search_url, timeout=5).json()

        ls_id = None
        # 규칙과 법률이 혼재될 수 있으므로 lawNm(법령명) 완전 일치 및 시행중인 법령 필터링
        law_list = search_res.get('LawSearch', {}).get('law', [])
        for law in law_list:
            if law.get('lawNm') == law_name:
                ls_id = law.get('lsId')
                break

        if not ls_id:
            return "해당 법령을 찾을 수 없습니다."

        # 2. 본문 조회로 상세 조문 확보
        body_url = f"https://www.law.go.kr/DRF/lawService.do?OC={API_KEY}&target=law&type=JSON&lsId={ls_id}"
        law_data = requests.get(body_url, timeout=5).json()

        # 3. 조문(Jo) 리스트에서 원하는 번호 추출 (계층 구조 반영 및 딕셔너리 예외 처리)
        law_body = law_data.get('Law', {})
        jo_list = law_body.get('Jo', [])

        # 조문이 1개일 경우 리스트가 아닌 딕셔너리로 반환되는 정부 API 고질적 이슈 대응
        if isinstance(jo_list, dict):
            jo_list = [jo_list]

        for jo in jo_list:
            if article_num in str(jo.get('joNo', '')):
                return jo.get('joCts', '')

        return "해당 조문을 찾을 수 없습니다."

    except requests.exceptions.Timeout:
        return "법령 서버 응답 지연으로 실시간 확인이 불가합니다."
    except Exception as e:
        return f"법령 조회 중 오류 발생: {str(e)}"
```

---

## 2부. 우리말샘 API

### [조건 기반 맞춤 작명 QA 시스템 - 순우리말 검증 파이프라인 전용]

이 문서는 LLM이 추천한 이름 후보가 **실제로 존재하는 순우리말(고유어) 명사인지 검증**하고 뜻풀이를 가져오기 위해 사용하는 우리말샘 API의 핵심 가이드입니다.

### 2.1 API 기본 정보

- **요청 URL**: `https://opendict.korean.go.kr/api/search`
- **통신 방식**: HTTP GET
- **데이터 포맷**: JSON (백엔드 파싱 최적화)
- **인증키**: 우리말샘 홈페이지에서 발급받은 16진수 32자리 문자열

### 2.2 요청 변수 (Request Parameters)

작명용 순우리말 명사를 정확히 골라내기 위해 쿼리스트링(`?변수=값&...`)에 포함해야 하는 필수 및 핵심 변수입니다.

| 변수명 | 필수 여부 | 설정값 (권장) | 설명 |
| --- | --- | --- | --- |
| **`key`** | **필수** | (32자리 인증키) | 본인의 오픈 API 인증키 |
| **`q`** | **필수** | (검색어) | LLM이 추천한 이름 후보 (예: 가람) |
| **`req_type`** | 선택 | `json` | 결과를 JSON 형태로 받기 위해 필수 설정 |
| **`method`** | 선택 | `exact` | **[핵심/추가]** 검색어가 포함된 단어가 아닌, **정확히 일치하는 단어만 검색** |
| **`advanced`** | 선택 | `y` | **[핵심]** 고유어, 품사 조건을 적용하기 위해 자세히 찾기 활성화 |
| **`target`** | 선택 | `1` | 표제어(단어)를 대상으로 검색 |
| **`type2`** | 선택 | `native` | **[핵심]** 한자어나 외래어를 제외하고 **고유어(순우리말)만 검색** |
| **`pos`** | 선택 | `1` | **[핵심]** 작명에 부적합한 동사/형용사 등을 제외하고 **명사만 검색** |

> 💡 **최종 요청 URL 예시**
> `https://opendict.korean.go.kr/api/search?key=여기에인증키입력&q=가람&req_type=json&method=exact&advanced=y&target=1&type2=native&pos=1`

### 2.3 출력 결과 필드 (Response Fields)

정상적으로 호출했을 때 돌아오는 JSON 구조에서 파이프라인에 꼭 필요한 데이터만 정리했습니다.

| 계층 구조 | 필드명 | 설명 | 환각(Hallucination) 판별 기준 |
| --- | --- | --- | --- |
| **`channel`** | `total` | 검색된 단어의 총 개수 | **이 값이 `0`이면 LLM이 지어낸 가짜 단어로 판정 (추천 제외)** |
|  | `num` | 현재 페이지 결과 수 | 기본값 10 |
| └ **`item`** | `word` | 검색된 단어 (표제어) | `q`로 보낸 검색어와 일치하는지 확인 |
|  | `sense` > `definition` | **단어의 뜻풀이** | 시스템 최종 답변의 추천 사유/근거 텍스트로 활용 |
|  | `sense` > `link` | 우리말샘 상세 페이지 링크 | 출처 URL (Source Label) 표기에 활용 |

### 2.4 사용 방법 및 다의어 처리 로직 (How to use)

FastMCP의 `external_api` 노드 등에서 데이터를 받아 처리하는 기준 흐름입니다.

#### ✅ 유효한 순우리말일 경우 (`total` >= 1)

```json
{
  "channel": {
    "total": 1,
    "item": [
      {
        "word": "가람",
        "sense": {
          "definition": " '강'의 옛말이자 순우리말 표현.",
          "pos": "명사",
          "link": "https://opendict.korean.go.kr/dictionary/view?sense_no=243179"
        }
      }
    ]
  }
}
```

- **단일 결과 (`total` == 1):** 
검증 통과. 
`definition`을 추출해 사용자에게 "가람은 강을 뜻하는 순우리말입니다."라고 답변.
- **다의어/동음이의어 (`total` > 1):** 
'가람1', '가람2' 등 의미가 여러 개일 수 있습니다. 
이 경우 `item` 리스트를 순회하며 모든 `definition`을 배열로 묶어 LLM에게 전달하고, LLM이 문맥상 가장 작명에 어울리는 뜻을 선택하도록 프롬프트를 구성하세요.

#### ❌ LLM이 지어낸 가짜 단어일 경우 (`total` == 0)

```json
{
  "channel": {
    "total": 0,
    "item": []
  }
}
```

- **처리 방법:** 
검색 결과가 없으므로 환각으로 간주. 해당 이름을 내부 추천 리스트에서 즉시 폐기하고 다른 후보 탐색.

### 2.5 자주 발생하는 에러 코드 (Debugging)

요청이 실패할 경우 JSON에 `error` 객체가 반환됩니다.

| **에러 코드** | **메시지** | **해결 방법** |
| --- | --- | --- |
| **020** | Unregistered key | 잘못된 인증키입니다. API 키 발급 상태를 확인하세요. |
| **100** | Incorrect query request | 검색어(`q`)가 비어있습니다. 검색 변수를 확인하세요. |
| **106** | Invalid advanced value | `advanced=y` 파라미터가 제대로 들어갔는지 확인하세요. |
| **201** | Invalid type2 value | `type2=native` 파라미터에 오타가 없는지 확인하세요. |
| **210** | Invalid pos value | `pos=1` (명사 조건) 파라미터를 확인하세요. |

### 2.6 시스템 연동 주의사항 (FastMCP)

- **Timeout 처리 필수:** 
공공 API 특성상 응답 지연이 발생할 수 있습니다. 
FastMCP Tool 구현 시 `requests.get(url, timeout=3)`과 같이 타임아웃을 설정하고, 에러 발생 시 시스템이 멈추지 않고 '뜻풀이 일시 확인 불가' 등으로 유연하게 넘어가도록 예외 처리(Try-Except)를 반드시 추가하세요.
