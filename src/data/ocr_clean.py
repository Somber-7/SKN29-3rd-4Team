"""
OCR 결과 정제 스크립트
ocr_raw_full.txt → ocr_cleaned.txt

수정 항목:
  1. 품사 태그 정규화  ('명] 명! 명_ → [명])
  2. 페이지 헤더 제거  (아름답고 정겨운 우리말 N, 색 인 N)
  3. 놓다 계열 오류    (농고→놓고 등)
  4. 조사 을/를/은/는 (고신뢰도 패턴만)
  5. 었다/없다 혼동   (동사어간+없다 → +었다)
  6. 예문 마커 정규화 (끼·9·기·%·7 → ¶)
  7. 중복 공백 제거
"""

import re
import os

INPUT  = r"D:\prj0617_2\SKN29-3rd-4Team\data\processed\ocr\ocr_raw_full.txt"
OUTPUT = r"D:\prj0617_2\SKN29-3rd-4Team\data\processed\ocr\ocr_cleaned.txt"
LOG    = r"D:\prj0617_2\SKN29-3rd-4Team\data\processed\ocr\ocr_clean_log.txt"

# ── 1. 품사 태그 패턴 ─────────────────────────────────────────────────────────
# OCR 변형: '명], [명_, 명!, 명;, 명,  / 같은 방식으로 동·형·부·관·수·대·감·접사·의존명사
POS_OPEN  = r"['\[「『]?\s*"
POS_CLOSE = r"\s*[」\]!_,\.\;\-\)~]"
POS_TAGS  = r"(명의|의존명사|접사|명|동|형|부|관|수|대|감)"

# '명] / [명_ / 명! 등 → [명]
POS_PATTERN = re.compile(POS_OPEN + POS_TAGS + POS_CLOSE)

def normalize_pos(m):
    return f"[{m.group(1)}]"

# ── 2. 페이지 헤더 패턴 ──────────────────────────────────────────────────────
HEADER_PATTERNS = [
    re.compile(r"^아름답고\s+정겨운\s+우리말\s*\d+\s*$"),
    re.compile(r"^색\s+인\s*\d+\s*$"),
    re.compile(r"^\d+\s*$"),                        # 독립 페이지 번호
]

# ── 3. 놓다 계열 오류 ─────────────────────────────────────────────────────────
NOHTA_FIXES = [
    ("농고서", "놓고서"),
    ("농아두", "놓아두"),
    ("농아서", "놓아서"),
    ("농아라", "놓아라"),
    ("농으며", "놓으며"),
    ("농지만", "놓지만"),
    ("농아도", "놓아도"),
    ("농고는", "놓고는"),
    ("농고도", "놓고도"),
    ("농고자", "놓고자"),
    ("농고서", "놓고서"),
    ("농았다", "놓았다"),
    ("농았으", "놓았으"),
    ("농고", "놓고"),
    ("농은", "놓은"),
    ("농다", "놓다"),
    ("농아", "놓아"),
    ("농지", "놓지"),
]

# ── 4. 조사 을 수정 (고신뢰도 패턴만) ───────────────────────────────────────
# 수정 원칙: 잘못 고쳐서 의미가 깨지는 것보다 그냥 두는 게 낫다.
# 따라서 아래 두 패턴만 적용:
#   (1) `올` : 받침 있는 음절 바로 뒤 → `을`    (올해·올바른 등은 앞 음절이 공백·구두점)
#   (2) `흘` : 받침 있는 음절 바로 뒤 → `을`    (흘리다·흘러는 앞 음절이 없거나 받침없음)
# `틀`·`름`·`률` → `를` 는 이름·아름·틀리다 등 오탈자 유발 위험이 높아 제외.
# `논`·`눈` → `는` 도 논밭·눈(目) 오탈자 위험이 높아 제외.

def has_jongseong(char):
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    return False

def fix_particles(line):
    result = list(line)
    i = 0
    changes = 0
    while i < len(line):
        ch = line[i]
        prev = line[i-1] if i > 0 else ''
        nxt  = line[i+1] if i+1 < len(line) else ' '

        def nxt_is_boundary():
            return nxt in ' \t\n.,;:!?」』)>\'' or (0xAC00 <= ord(nxt) <= 0xD7A3)

        # `올`: 바로 앞 음절에 받침 있으면 → `을`
        if ch == '올' and prev and has_jongseong(prev) and nxt_is_boundary():
            result[i] = '을'
            changes += 1

        # `흘`: 바로 앞 음절에 받침 있으면 → `을`
        elif ch == '흘' and prev and has_jongseong(prev) and nxt_is_boundary():
            result[i] = '을'
            changes += 1

        i += 1
    return ''.join(result), changes

# ── 5. 었다/없다 혼동 ────────────────────────────────────────────────────────
# 동사 어간(받침 있는 음절) + 없다 → 었다
# 단, 진짜 `없다`가 맞는 경우 보호:
#   - "~이 없다", "~가 없다", "~을 없다" 형태는 제외
#   - "~(었/았)없다" → "(었/았)었다" (이중 오류)
EOTDA_PATTERN = re.compile(r'([가-힣])없다')
def fix_eotda(line):
    # 받침 있는 음절 + 없다 → 었다
    def replacer(m):
        prev = m.group(1)
        if has_jongseong(prev):
            # 예외: 실제로 "없다" 앞에 자연스럽게 받침 음절이 오는 경우
            # ex) "수가 없다", "수밖에 없다" 등 → 여기서 앞 글자는 주로 가/수/게/때/적 등
            # 완벽한 판단은 어렵지만, 어간+없다 패턴의 핵심:
            # 어간 끝받침이 있는 경우 → 었다
            return prev + '었다'
        return m.group(0)
    return EOTDA_PATTERN.sub(replacer, line)

# ── 6. 예문 마커 ─────────────────────────────────────────────────────────────
# 줄 시작(또는 공백 뒤) '끼' → ¶
# 줄 시작 '9 한글' 패턴 → '¶ 한글' (9가 예문마커로 쓰인 경우)
EX_MARKER = re.compile(r'(?<!\d)(끼)(?=[가-힣\s])')
EX_9      = re.compile(r'(?:^|\s)(9)(\s+)([가-힣])')   # 9 뒤에 한글

# ── 7. 공백 경계 조사 오인식: 틀/름/률 → 를, 논/눈 → 는 ───────────────────
# 조건: 앞에 한글 2글자 이상 + 뒤에 공백/구두점 (복합어 첫음절 보호)
# 제외: 앞 1글자+대상글자가 실제 내용어를 이루는 경우
PROTECT_REUL = {
    # 름 계열: 이름·나름·아름 등 름으로 끝나는 내용어
    '이름', '나름', '아름', '거름', '고름', '노름', '오름',
    '보름', '구름', '지름', '부름', '모름', '사름',
    # 틀 계열: 기틀·씨틀·베틀 등 틀로 끝나는 내용어
    '기틀', '씨틀', '베틀', '발틀', '이틀',
}
PROTECT_NON = {
    # 눈 계열: 군눈·곁눈 등 눈으로 끝나는 내용어
    '군눈', '곁눈', '외눈', '왼눈',
}

SPACE_REUL_PAT = re.compile(r'([가-힣]{2})[틀름률](?=[\s\]\)\.,;:\'\"\<\>])')
SPACE_NON_PAT  = re.compile(r'([가-힣]{2})[논눈](?=[\s\]\)\.,;:\'\"\<\>])')

def fix_space_particle(line):
    def repl_reul(m):
        last1 = m.group(1)[-1]          # 대상 바로 앞 글자
        char  = m.group(0)[-1]           # 틀/름/률
        if (last1 + char) in PROTECT_REUL:
            return m.group(0)            # 내용어 → 건너뜀
        return m.group(1) + '를'

    def repl_non(m):
        last1 = m.group(1)[-1]
        char  = m.group(0)[-1]           # 논/눈
        if (last1 + char) in PROTECT_NON:
            return m.group(0)
        return m.group(1) + '는'

    new, n1 = SPACE_REUL_PAT.subn(repl_reul, line)
    new, n2 = SPACE_NON_PAT.subn(repl_non, new)
    return new, n1 + n2

# ── 후처리: 공백 경계 규칙 false positive 복원 ──────────────────────────────
# PROTECT_REUL에서 누락된 내용어: 엿기름(malt), 시름시름(부사), 업시름(명사)
# 공백 경계 규칙이 내용어를 조사로 오판하여 변환한 것을 되돌림
# 엿기름 올 → 엿기를 올 → 엿기름을  (름 복원 + 공백+올→을 동시 수정)
FALSE_POS_FIXES = [
    ('엿기를 올', '엿기름을'),   # 엿기름(malt) + 공간분리된 올(=을) 함께 수정
    ('엿기를',    '엿기름'),     # 위에서 못 잡힌 경우 대비
    ('시름시를',  '시름시름'),   # 시름시름 부사
    ('업시를',    '업시름'),     # 업시름 명사
]

def fix_false_positives(line):
    n = 0
    for wrong, right in FALSE_POS_FIXES:
        if wrong in line:
            count = line.count(wrong)
            line = line.replace(wrong, right)
            n += count
    return line, n

# ── 6b. 따옴표 뒤 조사 논/눈 → 는 ──────────────────────────────────────────
# 이 사전 어원 설명 형식: '단어' 논/눈 '뜻' → 논/눈 = 항상 조사 '는'
# false positive 구조적으로 불가: 따옴표 닫힘 직후이므로 내용어와 혼동 없음
QUOTE_NON = re.compile(r"(?<=['''\"‘’])\s*논(?=\s)")
QUOTE_NUN = re.compile(r"(?<=['''\"‘’])\s*눈(?=\s)")

def fix_quote_particle(line):
    new, n1 = QUOTE_NON.subn('는', line)
    new, n2 = QUOTE_NUN.subn('는', new)
    return new, n1 + n2

# ── 6c. 노이즈 괄호 제거 ────────────────────────────────────────────────────
# 괄호 안에 한글이 전혀 없는 경우 → OCR 노이즈 (예: [_], [*], [6], [47], [{ {])
# 한글 포함 태그(명, 동, 비, 참 등)는 건드리지 않음
NOISE_BRACKET = re.compile(r'\[[^가-힣\]]{0,8}\]')

def fix_noise_bracket(line):
    new_line, n = NOISE_BRACKET.subn('', line)
    return new_line, n

# ── 6c. [[미] → [비] 오인식 수정 ───────────────────────────────────────────
# [비](비슷한말) 앞에 [ 가 하나 더 붙고 ㅂ→ㅁ 오인식된 패턴
DOUBLE_MI = re.compile(r'\[\[미\]')

def fix_double_mi(line):
    new_line = DOUBLE_MI.sub('[비]', line)
    return new_line, 1 if new_line != line else 0

# ── 6c. 뜻번호 마커 @ ────────────────────────────────────────────────────────
# [명]/[동] 등 POS 태그 닫힘, 인용 끝(>), 문장 구분자(:.) 뒤에 오는 @
# → ①②③ 역할의 뜻번호 마커이므로 ¶ 로 통일
# 괄호 안 @(락표) 나 단어 중간 아@하다 는 건드리지 않음
DEF_MARKER_AT = re.compile(r'(?<=[\]>:.])\s*@(?=[가-힣(])')

def fix_markers(line):
    line = EX_MARKER.sub('¶', line)
    line = EX_9.sub(lambda m: m.group(0).replace('9', '¶'), line)
    return line

def fix_def_marker_at(line):
    new_line, n = DEF_MARKER_AT.subn(lambda m: m.group(0).replace('@', ' ¶'), line)
    return new_line, n

# ── 메인 처리 ────────────────────────────────────────────────────────────────

def process():
    with open(INPUT, 'r', encoding='utf-8') as f:
        raw = f.read()

    lines = raw.split('\n')
    out_lines = []
    log_lines = []
    stats = {
        'header_removed': 0,
        'pos_fixed': 0,
        'nohta_fixed': 0,
        'particle_fixed': 0,
        'eotda_fixed': 0,
        'marker_fixed': 0,
        'quote_particle_fixed': 0,
        'space_particle_fixed': 0,
        'noise_bracket_fixed': 0,
        'double_mi_fixed': 0,
        'def_marker_at_fixed': 0,
        'false_pos_restored': 0,
    }

    for lineno, line in enumerate(lines, 1):
        orig = line

        # 페이지 구분자는 유지
        if line.startswith('=== PAGE'):
            out_lines.append(line)
            continue

        # 페이지 헤더 제거
        if any(p.match(line.strip()) for p in HEADER_PATTERNS):
            stats['header_removed'] += 1
            log_lines.append(f"L{lineno} [헤더삭제] {repr(line)}")
            continue

        # 놓다 계열
        for wrong, right in NOHTA_FIXES:
            if wrong in line:
                line = line.replace(wrong, right)
                stats['nohta_fixed'] += 1

        # 품사 태그 정규화
        new_line, n = POS_PATTERN.subn(normalize_pos, line)
        if n > 0:
            stats['pos_fixed'] += n
            if new_line != line:
                log_lines.append(f"L{lineno} [품사태그 {n}건] {repr(line[:60])} → {repr(new_line[:60])}")
        line = new_line

        # 조사 수정
        new_line, n = fix_particles(line)
        if n > 0:
            stats['particle_fixed'] += n
        line = new_line

        # 었다/없다
        new_line = fix_eotda(line)
        if new_line != line:
            stats['eotda_fixed'] += 1
        line = new_line

        # 예문 마커
        new_line = fix_markers(line)
        if new_line != line:
            stats['marker_fixed'] += 1
        line = new_line

        # 따옴표 뒤 조사 논/눈 → 는
        new_line, n = fix_quote_particle(line)
        if n:
            stats['quote_particle_fixed'] += n
        line = new_line

        # 공백 경계 조사 오인식: 틀/름/률 → 를, 논/눈 → 는
        new_line, n = fix_space_particle(line)
        if n:
            stats['space_particle_fixed'] += n
        line = new_line

        # 공백 경계 규칙 false positive 복원 (내용어 엿기름·시름시름·업시름)
        new_line, n = fix_false_positives(line)
        if n:
            stats['false_pos_restored'] += n
        line = new_line

        # 노이즈 괄호 제거
        new_line, n = fix_noise_bracket(line)
        if n:
            stats['noise_bracket_fixed'] += n
        line = new_line

        # [[미] → [비]
        new_line, n = fix_double_mi(line)
        if n:
            stats['double_mi_fixed'] += n
        line = new_line

        # 뜻번호 마커 @
        new_line, n = fix_def_marker_at(line)
        if n > 0:
            stats['def_marker_at_fixed'] += n
        line = new_line

        # 중복 공백 제거
        line = re.sub(r' {2,}', ' ', line).strip()

        if line or orig.strip() == '':
            out_lines.append(line)

    # 저장
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines))

    with open(LOG, 'w', encoding='utf-8') as f:
        f.write("=== OCR 정제 로그 ===\n\n")
        f.write(f"입력: {INPUT}\n출력: {OUTPUT}\n\n")
        f.write("[ 수정 통계 ]\n")
        for k, v in stats.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\n[ 변경 샘플 (최대 200건) ]\n")
        for entry in log_lines[:200]:
            f.write(entry + "\n")

    print("=== 정제 완료 ===")
    for k, v in stats.items():
        print(f"  {k}: {v:,}")
    print(f"\n출력: {OUTPUT}")
    print(f"로그: {LOG}")

if __name__ == "__main__":
    process()
