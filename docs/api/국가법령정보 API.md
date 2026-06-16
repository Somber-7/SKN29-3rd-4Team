# 국가법령정보 API

생성일: 2026년 6월 10일 오후 4:28

# 📖 국가법령정보 오픈 API 사용설명서

## 1. 프로젝트 활용 프로세스 (2단계 아키텍처)

국가법령정보 API는 법령의 ID(일련번호)를 알아야 본문을 조회할 수 있으므로, 반드시 아래 **2단계 호출 순서**를 따라야 합니다.

1. **목록 조회 (Search)**: 법령명(예: "가족관계의 등록 등에 관한 규칙")으로 검색하여 `lsId`(법령일련번호)를 획득.
2. **본문 조회 (Get Body)**: 확보한 `lsId`로 법령 본문을 호출하여 실제 조항 텍스트 추출.

## 2. API 상세 명세

### 📌 1단계: 현행법령 목록 조회 API

- **요청 URL**: `https://www.law.go.kr/DRF/lawSearch.do`

| **변수명** | **설정값** | **설명** |
| --- | --- | --- |
| **`OC`** | (인증키) | 국가법령정보센터 발급 ID |
| **`target`** | `law` | 검색 대상 (법령) |
| **`type`** | `JSON` | **[필수]** 결과 포맷 지정 |
| **`query`** | (검색어) | 법령명 (예: 가족관계의 등록 등에 관한 법률) |

### 📌 2단계: 현행법령 본문 조회 API

- **요청 URL**: `https://www.law.go.kr/DRF/lawService.do`

| **변수명** | **설정값** | **설명** |
| --- | --- | --- |
| **`OC`** | (인증키) | 사용자 인증 ID |
| **`target`** | `law` | 검색 대상 |
| **`type`** | `JSON` | **[필수]** |
| **`lsId`** | (법령ID) | 1단계에서 획득한 `lsId` 값 |

## 3. 출력 결과 (JSON) 및 추출 필드

| **데이터 계층** | **필드명** | **용도 및 프로젝트 활용 방안** |
| --- | --- | --- |
| **`Law`** | `BasicInfo` | 법령 명칭, 시행일(`efYd`) 등 기본 정보 확인 |
| **`Jo`** | `joNo` | **조문 번호**. (출처 라벨링: "제44조") |
|  | `joCts` | **조문 내용**. (RAG의 근거 문맥으로 LLM에 제공) |
| **`Byl`** | `bylSeq` | 별표 정보 (필요시 참고용 데이터) |

## 4. 우리 프로젝트 적용 시나리오 (수정된 예시 코드)

`law_server.py`에서 법령 정보를 가져오는 핵심 로직입니다. JSON 계층 구조(KeyError 방지)와 정확한 법령 매칭 로직이 반영되었습니다.

```python
import requests

def get_law_content(law_name: str, article_num: str):
    try:
        # 1. 목록 조회로 lsId 획득
        search_url = f"[<https://www.law.go.kr/DRF/lawSearch.do?OC=>](<https://www.law.go.kr/DRF/lawSearch.do?OC=>){API_KEY}&target=law&type=JSON&query={law_name}"
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
        body_url = f"[<https://www.law.go.kr/DRF/lawService.do?OC=>](<https://www.law.go.kr/DRF/lawService.do?OC=>){API_KEY}&target=law&type=JSON&lsId={ls_id}"
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