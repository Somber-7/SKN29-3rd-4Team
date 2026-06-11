# 한자 유니코드 오행 JSON/CSV 정제 작업 기록

**작성일**: 2026-06-11  
**작업 범위**: 인명용 한자 유니코드/오행 데이터 정제 및 CSV 산출물 생성  
**대상 폴더**: `data/processed/unihan_maping/`

## 1. 작업 목적

인명용 한자 데이터의 `sound_meaning` 값에서 한자의 음과 뜻이 제대로 분리되지 않은 항목을 정리하고, 원본 PDF 자료와 교차검증한 최종 JSON/CSV 산출물을 만드는 것이 목적이다.

최종 데이터는 이후 이름 추천 QA 시스템에서 한자별 의미, 획수, 발음오행, 자원오행을 조회하거나 벡터 DB/관계형 DB에 적재할 때 기준 데이터로 사용된다.

## 2. 참고 원본 자료

다음 원본 PDF 파일을 기준 자료로 사용했다.

- `data/raw/pdf/한글 글자 유니코드.pdf`
- `data/raw/pdf/hanja.pdf`

## 3. 최종 JSON 기준 구조

최종 JSON은 다음 8개 필드를 유지했다.

| 필드 | 설명 |
| --- | --- |
| `profile_id` | 한자 프로필 고유 ID |
| `hangul` | 한자의 대표 한글 음 |
| `hanja` | 한자 |
| `unicode` | 한자 유니코드 코드포인트 |
| `sound_meaning` | 한자의 뜻 |
| `strokes` | 획수 |
| `sound_ohaeng` | 발음오행 |
| `resource_ohaeng` | 자원오행 |

예시:

```json
{
  "profile_id": "OHE-00001",
  "hangul": "가",
  "hanja": "加",
  "unicode": "U+52A0",
  "sound_meaning": "더할",
  "strokes": 5,
  "sound_ohaeng": "목",
  "resource_ohaeng": "목"
}
```

## 4. 처리 과정

1. `hanja_unicode_ohaeng_verified_corrected.json`의 전체 구조와 필수 필드를 확인했다.
2. 한자의 음과 뜻이 함께 섞여 있거나 분리 상태가 불안정한 항목을 우선 점검했다.
3. `한글 글자 유니코드.pdf`와 `hanja.pdf`를 기준으로 한글 음, 한자, 유니코드, 획수, 뜻 정보를 교차검증했다.
4. 확인된 오류 항목을 최종 JSON에 반영했다.
5. JSON 전체 행 수, 필드 구성, 유니코드 코드포인트 일치 여부를 재검증했다.
6. 최종 JSON을 기준으로 전체 CSV 파일을 생성했다.
7. 검색 및 후속 처리 편의를 위해 필드별 CSV 파일을 별도로 분리했다.
8. 중간 백업 파일과 작업 임시 파일은 정리하고 최종 산출물만 남겼다.

## 5. 대표 수정 및 검증 항목

작업 중 다음 항목들을 대표적으로 확인했다.

| profile_id | hangul | hanja | unicode | 주요 확인 내용 |
| --- | --- | --- | --- | --- |
| `OHE-00254` | `구` | `耈` | `U+8008` | 한자와 유니코드 매핑 확인 |
| `OHE-00681` | `만` | `晚` | `U+665A` | 한자와 한글 음 확인 |
| `OHE-01454` | `울` | `蔚` | - | `sound_meaning`을 `우거질`로 정리 |
| `OHE-01740` | `정` | `淨` | `U+6DE8` | 획수 11, 발음오행 `금` 확인 |

## 6. 최종 산출물

최종 산출물은 `data/processed/unihan_maping/` 아래에 정리했다.

| 파일 | 설명 | 행 수 |
| --- | --- | --- |
| `hanja_unicode_ohaeng_verified_corrected.json` | 최종 정제 JSON | 2420 |
| `hanja_unicode_ohaeng_verified_corrected.csv` | 최종 정제 전체 CSV | 2420 |

필드별 CSV 산출물은 `data/processed/unihan_maping/csv_tables/` 아래에 정리했다.

| 파일 | 컬럼 | 행 수 |
| --- | --- | --- |
| `profile_id.csv` | `profile_id` | 2420 |
| `profile_hangul.csv` | `profile_id`, `hangul` | 2420 |
| `profile_hanja.csv` | `profile_id`, `hanja` | 2420 |
| `profile_unicode.csv` | `profile_id`, `unicode` | 2420 |
| `profile_sound_meaning.csv` | `profile_id`, `sound_meaning` | 2420 |
| `profile_strokes.csv` | `profile_id`, `strokes` | 2420 |
| `profile_sound_ohaeng.csv` | `profile_id`, `sound_ohaeng` | 2420 |
| `profile_resource_ohaeng.csv` | `profile_id`, `resource_ohaeng` | 2420 |

## 7. 검증 결과

- 최종 JSON 총 행 수: 2420
- 최종 전체 CSV 총 행 수: 2420
- 필드별 CSV 총 행 수: 각 2420
- 최종 JSON 필드: `profile_id`, `hangul`, `hanja`, `unicode`, `sound_meaning`, `strokes`, `sound_ohaeng`, `resource_ohaeng`
- 첫 번째 데이터: `OHE-00001`, `加`, `가`, `U+52A0`, `더할`
- 마지막 데이터: `OHE-02420`, `曦`, `희`, `U+66E6`, `햇빛`
- 최종 폴더에는 JSON, 전체 CSV, 필드별 CSV 산출물만 유지했다.

## 8. 후속 사용 방향

이 데이터는 한자 메타데이터의 기준 원본으로 사용한다.  
ChromaDB, SQLite, Neo4j 등 후속 저장소에 적재할 때는 이 파일을 직접 수정하지 않고, 별도 변환 산출물을 생성해서 사용하는 방식이 안전하다.
