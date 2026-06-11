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

INPUT  = r"D:\prj0617\SKN29-3rd-4Team\data\processed\ocr\ocr_raw_full.txt"
OUTPUT = r"D:\prj0617\SKN29-3rd-4Team\data\processed\ocr\ocr_cleaned.txt"
LOG    = r"D:\prj0617\SKN29-3rd-4Team\data\processed\ocr\ocr_clean_log.txt"

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

def fix_markers(line):
    line = EX_MARKER.sub('¶', line)
    # 9가 예문 마커로 사용된 경우 (행 앞 또는 공백 뒤)
    line = EX_9.sub(lambda m: m.group(0).replace('9', '¶'), line)
    return line

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
