import sys
import io
import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

# Fix Unicode output on Windows (avoid cp1252 errors)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Patterns of channels to skip validation
SKIP_PATTERNS = ['Mana-mana', 'tonton']

# Shared headers for all HTTP requests
DEFAULT_HEADERS = {'User-Agent': 'VLC/3.0.20'}

# Retry settings
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds between retries

# Stream types written as direct URL into Main.m3u8
DIRECT_URL_TYPES = ('hls_master', 'dash', 'mp4')

# Malaysia timezone (UTC+8)
MYT = timezone(timedelta(hours=8))

# =============================================================================
# REPORT FORMATTING
# =============================================================================

C_NUM  = 4     # row number
C_CH   = 30    # channel name
C_GRP  = 14    # group
C_STA  = 8     # status
C_TYPE = 14    # stream type
C_ENTR = 14    # main entry
C_URLS = 6     # urls tested
C_TRY  = 5     # attempts

SEP_WIDE  = '  ' + '═' * 100
SEP_THIN  = '  ' + '─' * 100
SEP_DASH  = ('  '
             + '─' * C_NUM  + '  '
             + '─' * C_CH   + '  '
             + '─' * C_GRP  + '  '
             + '─' * C_STA  + '  '
             + '─' * C_TYPE + '  '
             + '─' * C_ENTR + '  '
             + '─' * C_URLS + '  '
             + '─' * C_TRY)


def trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + '\u2026'  # … ellipsis


def fmt_row(num, ch, grp, sta, typ, ent, urls, tries):
    return (
        f"  {str(num):>{C_NUM}}  "
        f"{trunc(ch,  C_CH):<{C_CH}}  "
        f"{trunc(grp, C_GRP):<{C_GRP}}  "
        f"{sta:<{C_STA}}  "
        f"{trunc(typ, C_TYPE):<{C_TYPE}}  "
        f"{ent:<{C_ENTR}}  "
        f"{urls:<{C_URLS}}  "
        f"{str(tries):>{C_TRY}}"
    )


def fmt_header():
    return fmt_row('No.', 'Channel', 'Group',
                   'Status', 'Type', 'Entry', 'URLs', 'Tries')


def write_section(f, title, entries):
    if not entries:
        return
    f.write(f'\n{SEP_WIDE}\n')
    f.write(f'  {title} ({len(entries)})\n')
    f.write(f'{SEP_WIDE}\n\n')
    f.write(fmt_header() + '\n')
    f.write(SEP_DASH + '\n')
    for e in entries:
        f.write(fmt_row(
            e['num'],
            e['channel'],
            e['group'],
            e['status'],
            e['type'],
            e['entry'],
            e['urls'],
            e['tries']
        ) + '\n')


def write_report(report_path, entries, run_time_str,
                 total, count_active, count_dead,
                 count_skipped, success_pct,
                 count_wrapper, count_direct):

    active  = [e for e in entries if e['status'] == 'active']
    dead    = [e for e in entries if e['status'] == 'dead']
    skipped = [e for e in entries if e['status'] == 'skipped']

    with open(report_path, 'w', encoding='utf-8') as f:

        # ── Header ────────────────────────────────────────────────────
        f.write(f'\n{SEP_WIDE}\n')
        f.write( '  IPTV Playlist Validation Report\n')
        f.write(f'{SEP_WIDE}\n')
        f.write(f'  Generated  :  {run_time_str}\n')
        f.write(f'  Source     :  Channels/Flatten.m3u8\n')
        f.write(f'  Total      :  {total} channels\n')
        f.write(f'  Result     :  '
                f'{count_active} active  |  '
                f'{count_dead} dead  |  '
                f'{count_skipped} skipped  |  '
                f'{success_pct}% success\n')
        f.write(f'{SEP_THIN}\n')
        f.write( '  Active breakdown:\n')
        f.write(f'    Wrapper kept  :  {count_wrapper}  '
                f'(stream URL hidden behind wrapper)\n')
        f.write(f'    Direct URL    :  {count_direct}  '
                f'(hls_master / dash / mp4 — URL in Main.m3u8)\n')
        f.write(f'{SEP_THIN}\n')
        f.write( '  KEY\n')
        f.write( '    Status  :  active   = stream is working\n')
        f.write( '               dead     = no working stream found\n')
        f.write( '               skipped  = token-based (Mana-mana / tonton)\n')
        f.write( '    Entry   :  wrapper_kept = URL hidden, TV follows wrapper chain\n')
        f.write( '               direct_url   = URL written directly to Main.m3u8\n')
        f.write( '    URLs    :  e.g. 2/3 = 2nd of 3 backup links worked\n')
        f.write( '    Tries   :  HTTP attempts before pass or fail\n')
        f.write(f'{SEP_WIDE}\n')

        # ── Sections ──────────────────────────────────────────────────
        write_section(f, 'ACTIVE CHANNELS', active)
        write_section(f, 'DEAD CHANNELS',   dead)
        write_section(f, 'SKIPPED CHANNELS', skipped)

        # ── Summary ───────────────────────────────────────────────────
        f.write(f'\n{SEP_WIDE}\n')
        f.write( '  SUMMARY\n')
        f.write(f'{SEP_WIDE}\n')
        f.write(f'  {"Total":<14}  {total}\n')
        f.write(f'  {"Active":<14}  {count_active}\n')
        f.write(f'  {"  Wrapper kept":<14}  {count_wrapper}\n')
        f.write(f'  {"  Direct URL":<14}  {count_direct}\n')
        f.write(f'  {"Dead":<14}  {count_dead}\n')
        f.write(f'  {"Skipped":<14}  {count_skipped}\n')
        f.write(f'  {"Success":<14}  {success_pct}%\n')
        f.write(f'  {"Run time":<14}  {run_time_str}\n')
        f.write(f'{SEP_WIDE}\n')


# =============================================================================
# URL EXTRACTION
# =============================================================================

def extract_all_urls_from_wrapper(wrapper_content, base_url):
    urls = []
    for line in wrapper_content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('http'):
            urls.append(line)
        elif line.startswith('/'):
            urls.append(urljoin(base_url, line))
    return urls


# =============================================================================
# STREAM CLASSIFICATION
# =============================================================================

def classify_content(text, status, content_type):
    if text is None:
        return 'invalid'
    text = text.strip()
    if not text:
        return 'empty_ok'
    if text.startswith('#EXTM3U'):
        if '#EXT-X-STREAM-INF' in text:
            return 'hls_master'
        if '#EXTINF' in text:
            return 'hls_media'
        return 'wrapper'
    if text.startswith('\x00\x00\x00'):
        return 'mp4' if 'ftyp' in text else 'binary'
    if '<html' in text.lower() or '<!doctype' in text.lower():
        return 'html'
    if '404' in text or 'not found' in text.lower():
        return 'invalid'
    if text.startswith('<?xml') or text.startswith('<MPD'):
        return 'dash'
    if content_type and 'video/mp4' in content_type:
        return 'mp4'
    if content_type and 'application/dash+xml' in content_type:
        return 'dash'
    return 'invalid'


# =============================================================================
# STREAM VALIDATION
# =============================================================================

def validate_stream(stream_session, url):
    kind        = 'exception'
    http_status = 0

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = stream_session.get(
                url, headers=DEFAULT_HEADERS, timeout=15, stream=True
            )
            http_status  = resp.status_code
            chunk        = resp.raw.read(8192, decode_content=True)
            content_type = resp.headers.get('Content-Type', '')
            kind         = classify_content(
                chunk.decode('utf-8', errors='ignore'),
                http_status, content_type
            )
            good = kind not in (
                'invalid', 'html', 'empty_ok', 'binary', 'wrapper'
            )

            if good:
                return kind, http_status, True, attempt
            if http_status in (401, 403, 404, 410):
                return kind, http_status, False, attempt
            if attempt <= MAX_RETRIES:
                print(f"    ⚠️  Attempt {attempt} '{kind}' "
                      f"(HTTP {http_status}), retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
                continue
            return kind, http_status, False, attempt

        except Exception as e:
            if attempt <= MAX_RETRIES:
                print(f"    ⚠️  Attempt {attempt} exception: {e}, "
                      f"retrying in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
                continue
            return 'exception', 0, False, attempt

    return kind, http_status, False, MAX_RETRIES + 1


# =============================================================================
# SKIP CHECK
# =============================================================================

def is_skipped(channel_name, wrapper_url=''):
    for pat in SKIP_PATTERNS:
        if pat.lower() in channel_name.lower():
            return True
        if pat.lower() in wrapper_url.lower():
            return True
    return False


# =============================================================================
# FLATTEN.M3U8 PARSER
# =============================================================================

def parse_flatten(flatten_path):
    with open(flatten_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    header = ''
    if lines and lines[0].startswith('#EXTM3U'):
        header  = lines[0].strip()
        content = ''.join(lines[1:])
    else:
        content = ''.join(lines)

    blocks  = []
    pattern = re.compile(r'(#EXTINF:[^\n]*\n)((?:[^#\n][^\n]*\n?)+)')
    for m in pattern.finditer(content):
        extinf    = m.group(1)
        url_lines = m.group(2).strip()
        name_m    = re.search(r'tvg-name="([^"]*)"', extinf)
        grp_m     = re.search(r'group-title="([^"]*)"', extinf)
        blocks.append((
            m.group(0), extinf, url_lines,
            name_m.group(1) if name_m else 'Unknown',
            grp_m.group(1)  if grp_m  else ''
        ))
    return header, blocks


# =============================================================================
# MAIN.M3U8 WRITER
# =============================================================================

def update_main_m3u8(main_path, header, blocks, results):
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write((header or '#EXTM3U') + '\n')
        for idx, (full, extinf, url, name, group) in enumerate(blocks):
            if is_skipped(name, url):
                f.write(full)
                continue
            result = results[idx]
            if result['valid']:
                if result['direct']:
                    f.write(extinf)
                    f.write(result['direct'] + '\n')
                else:
                    f.write(full)
            else:
                for line in full.splitlines(keepends=True):
                    f.write('## ' + line)


# =============================================================================
# MAIN
# =============================================================================

def main():
    base_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path    = os.path.join(base_dir, 'Main.m3u8')
    report_path  = os.path.join(base_dir, 'validation-report.txt')

    run_time     = datetime.now(MYT)
    run_time_str = run_time.strftime('%Y-%m-%d %H:%M:%S MYT')

    print(f"Parsing  : {flatten_path}")
    header, blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Channels : {total}")
    print(f"Run time : {run_time_str}\n")

    results        = [{} for _ in blocks]
    report_entries = []

    count_active       = 0
    count_dead         = 0
    count_skipped      = 0
    count_direct       = 0
    count_wrapper_kept = 0

    # Persistent sessions — one DNS lookup each for the entire run
    wrapper_session = requests.Session()
    wrapper_session.headers.update(DEFAULT_HEADERS)
    stream_session  = requests.Session()
    stream_session.headers.update(DEFAULT_HEADERS)

    for i, (full, extinf, url, name, group) in enumerate(blocks):
        print('=' * 60)
        print(f"[{i+1}/{total}] {name}  (group: {group})")

        # ── Skipped ───────────────────────────────────────────────────
        if is_skipped(name, url):
            results[i] = {'valid': True, 'direct': None}
            report_entries.append({
                'num': i + 1, 'channel': name, 'group': group,
                'status': 'skipped', 'type': '-',
                'entry': '-', 'urls': '-', 'tries': '-'
            })
            count_skipped += 1
            print("Action  : ⏭️  Skipped (Mana-mana / tonton)")
            continue

        print(f"Wrapper : {url.strip()}")

        # ── Fetch wrapper ─────────────────────────────────────────────
        try:
            wr = wrapper_session.get(url.strip(), timeout=10)
            print(f"Wrapper HTTP: {wr.status_code}")

            if wr.status_code != 200:
                results[i] = {'valid': False, 'direct': None}
                report_entries.append({
                    'num': i + 1, 'channel': name, 'group': group,
                    'status': 'dead',
                    'type': f'wrapper_http_{wr.status_code}',
                    'entry': 'dead', 'urls': '0/0', 'tries': '1'
                })
                count_dead += 1
                print("Action  : ❌ Commented out (wrapper HTTP error)")
                continue

            wrapper_content = wr.text

        except Exception as e:
            results[i] = {'valid': False, 'direct': None}
            report_entries.append({
                'num': i + 1, 'channel': name, 'group': group,
                'status': 'dead', 'type': 'wrapper_exception',
                'entry': 'dead', 'urls': '0/0', 'tries': '1'
            })
            count_dead += 1
            print(f"Wrapper exception: {e}")
            print("Action  : ❌ Commented out (wrapper fetch failed)")
            continue

        # ── Extract all URLs ──────────────────────────────────────────
        inner_urls = extract_all_urls_from_wrapper(
            wrapper_content, url.strip()
        )
        total_urls = len(inner_urls)

        if total_urls == 0:
            results[i] = {'valid': False, 'direct': None}
            report_entries.append({
                'num': i + 1, 'channel': name, 'group': group,
                'status': 'dead', 'type': 'no_urls_in_wrapper',
                'entry': 'dead', 'urls': '0/0', 'tries': '0'
            })
            count_dead += 1
            print("Inner URLs : NONE FOUND")
            print("Action     : ❌ Commented out (no URLs in wrapper)")
            continue

        print(f"Inner URLs : {total_urls} found")

        # ── Test each URL ─────────────────────────────────────────────
        channel_valid    = False
        winning_url      = ''
        winning_kind     = ''
        winning_attempt  = 0
        winning_position = 0

        for url_idx, inner_url in enumerate(inner_urls, start=1):
            print(f"  [{url_idx}/{total_urls}] {inner_url}")
            stream_kind, http_status, good, attempt = validate_stream(
                stream_session, inner_url
            )
            print(f"  Result : {stream_kind} "
                  f"(HTTP {http_status}, attempt {attempt})")

            if good:
                channel_valid    = True
                winning_url      = inner_url
                winning_kind     = stream_kind
                winning_attempt  = attempt
                winning_position = url_idx
                print(f"  ✅ Working URL at position {url_idx}/{total_urls}")
                break
            else:
                print(f"  ❌ Failed ({stream_kind}, HTTP {http_status})")

        # ── Record result ─────────────────────────────────────────────
        if channel_valid:
            if winning_kind in DIRECT_URL_TYPES:
                results[i] = {'valid': True, 'direct': winning_url}
                main_entry  = 'direct_url'
                count_direct += 1
                action_note = f"direct URL written ({winning_kind})"
            else:
                results[i] = {'valid': True, 'direct': None}
                main_entry  = 'wrapper_kept'
                count_wrapper_kept += 1
                action_note = f"wrapper kept ({winning_kind})"

            report_entries.append({
                'num': i + 1, 'channel': name, 'group': group,
                'status': 'active', 'type': winning_kind,
                'entry': main_entry,
                'urls': f'{winning_position}/{total_urls}',
                'tries': str(winning_attempt)
            })
            count_active += 1
            print(f"Action  : ✅ {action_note}  "
                  f"(URL {winning_position}/{total_urls}, "
                  f"attempt {winning_attempt})")
        else:
            results[i] = {'valid': False, 'direct': None}
            report_entries.append({
                'num': i + 1, 'channel': name, 'group': group,
                'status': 'dead', 'type': 'all_urls_failed',
                'entry': 'dead',
                'urls': f'0/{total_urls}',
                'tries': str(MAX_RETRIES + 1)
            })
            count_dead += 1
            print(f"Action  : ❌ Commented out "
                  f"(all {total_urls} URL(s) failed)")

    # Close sessions
    wrapper_session.close()
    stream_session.close()

    # Summary
    non_skipped = total - count_skipped
    success_pct = round(count_active / max(non_skipped, 1) * 100, 1)

    print('\n' + '=' * 60)
    print(f"Results  : {count_active} active | "
          f"{count_dead} dead | {count_skipped} skipped")
    print(f"  Wrapper kept : {count_wrapper_kept}  |  "
          f"Direct URL : {count_direct}")
    print(f"Success  : {success_pct}%")

    # Write formatted report
    print("Writing validation-report.txt...")
    write_report(
        report_path, report_entries, run_time_str,
        total, count_active, count_dead,
        count_skipped, success_pct,
        count_wrapper_kept, count_direct
    )

    # Write Main.m3u8
    print("Writing Main.m3u8...")
    update_main_m3u8(main_path, header, blocks, results)
    print("Done.")


if __name__ == '__main__':
    main()
