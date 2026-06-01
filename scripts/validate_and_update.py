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

# Patterns of channels to skip validation (Mana-mana and tonton)
SKIP_PATTERNS = ['Mana-mana', 'tonton']

# Shared default headers for HTTP requests
DEFAULT_HEADERS = {'User-Agent': 'VLC/3.0.20'}

# Retry settings
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds between retries

# Stream types that cannot be served via wrapper on TV apps.
# These get their direct URL written into Main.m3u8 instead.
DIRECT_URL_TYPES = ('hls_master', 'dash', 'mp4')

# Malaysia timezone (UTC+8)
MYT = timezone(timedelta(hours=8))


# =============================================================================
# URL / HEADER EXTRACTION
# =============================================================================

def extract_all_urls_from_wrapper(wrapper_content, base_url):
    """Extract ALL stream URLs from a wrapper .m3u8 file, in order."""
    urls = []
    for line in wrapper_content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#'):
            continue
        if line.startswith('http'):
            urls.append(line)
        elif line.startswith('/'):
            urls.append(urljoin(base_url, line))
    return urls


def extract_extvlcopt_headers(wrapper_content):
    """Extract #EXTVLCOPT lines from wrapper content and map them to HTTP headers."""
    option_lines = []
    headers = {}

    for raw_line in wrapper_content.splitlines():
        line = raw_line.strip()
        if not line.startswith('#EXTVLCOPT:'):
            continue

        option_lines.append(line + '\n')

        payload = line[len('#EXTVLCOPT:'):].strip()
        if '=' not in payload:
            continue

        key, value = payload.split('=', 1)
        key = key.strip().lower()
        value = value.strip()

        if key == 'http-referrer':
            headers['Referer'] = value
        elif key == 'http-user-agent':
            headers['User-Agent'] = value

    return option_lines, headers


def merge_option_lines(block_option_lines, wrapper_option_lines):
    """Merge and dedupe #EXTVLCOPT or other inline option lines."""
    merged = []
    seen = set()

    for source in (block_option_lines, wrapper_option_lines):
        for line in source:
            normalized = line.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized + '\n')

    return merged


# =============================================================================
# STREAM CLASSIFICATION
# =============================================================================

def classify_content(text, status, content_type):
    """Classify stream based on content snippet and HTTP metadata."""
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
        if 'ftyp' in text:
            return 'mp4'
        return 'binary'

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

def validate_stream(stream_session, url, extra_headers=None):
    """Fetch a stream URL and classify it. Retries up to MAX_RETRIES times.
    Uses wrapper-derived headers when provided.
    Returns (kind, http_status, good, attempt_number).
    """
    kind = 'exception'
    http_status = 0

    request_headers = DEFAULT_HEADERS.copy()
    if extra_headers:
        request_headers.update(extra_headers)

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = stream_session.get(
                url,
                headers=request_headers,
                timeout=15,
                stream=True
            )
            http_status = resp.status_code
            chunk = resp.raw.read(8192, decode_content=True)
            content_type = resp.headers.get('Content-Type', '')
            kind = classify_content(
                chunk.decode('utf-8', errors='ignore'),
                http_status,
                content_type
            )

            good = kind not in ('invalid', 'html', 'empty_ok', 'binary', 'wrapper')

            if good:
                return kind, http_status, True, attempt

            if http_status in (401, 403, 404, 410):
                return kind, http_status, False, attempt

            if attempt <= MAX_RETRIES:
                print(
                    f"    ⚠️  Attempt {attempt} returned '{kind}' "
                    f"(HTTP {http_status}), retrying in {RETRY_DELAY}s..."
                )
                time.sleep(RETRY_DELAY)
                continue

            return kind, http_status, False, attempt

        except Exception as e:
            kind = 'exception'
            if attempt <= MAX_RETRIES:
                print(
                    f"    ⚠️  Attempt {attempt} exception: {e}, "
                    f"retrying in {RETRY_DELAY}s..."
                )
                time.sleep(RETRY_DELAY)
                continue
            return kind, 0, False, attempt

    return kind, http_status, False, MAX_RETRIES + 1


# =============================================================================
# SKIP CHECK
# =============================================================================

def is_skipped(channel_name, wrapper_url=''):
    """Return True if this channel should skip validation."""
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
    """Parse Flatten.m3u8 and return (header_line, list_of_blocks).

    Each block is a dict with:
    - full
    - extinf
    - option_lines
    - url_lines
    - wrapper_url
    - channel_name
    - group
    """
    with open(flatten_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    header = ''
    start_index = 0
    if lines and lines[0].startswith('#EXTM3U'):
        header = lines[0].strip()
        start_index = 1

    blocks = []
    i = start_index
    total_lines = len(lines)

    while i < total_lines:
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith('##'):
            i += 1
            continue

        if not line.startswith('#EXTINF'):
            i += 1
            continue

        extinf = line
        option_lines = []
        url_lines = []
        full_lines = [extinf]

        name_match = re.search(r'tvg-name=\"([^\"]*)\"', extinf)
        channel_name = name_match.group(1) if name_match else 'Unknown'
        group_match = re.search(r'group-title=\"([^\"]*)\"', extinf)
        group = group_match.group(1) if group_match else ''

        i += 1
        while i < total_lines:
            current = lines[i]
            current_stripped = current.strip()

            if not current_stripped:
                i += 1
                continue

            if current.startswith('##'):
                i += 1
                continue

            if current.startswith('#EXTM3U'):
                i += 1
                continue

            if current.startswith('#EXTINF'):
                break

            if current.startswith('#'):
                option_lines.append(current)
                full_lines.append(current)
                i += 1
                continue

            url_lines.append(current)
            full_lines.append(current)
            i += 1

        wrapper_url = url_lines[0].strip() if url_lines else ''

        blocks.append({
            'full': ''.join(full_lines),
            'extinf': extinf,
            'option_lines': option_lines,
            'url_lines': url_lines,
            'wrapper_url': wrapper_url,
            'channel_name': channel_name,
            'group': group,
        })

    return header, blocks


# =============================================================================
# MAIN.M3U8 WRITER
# =============================================================================

def update_main_m3u8(main_path, header, blocks, results):
    """Write Main.m3u8 using the original EPG header line."""
    with open(main_path, 'w', encoding='utf-8') as f:
        if header:
            f.write(header + '\n')
        else:
            f.write('#EXTM3U\n')

        for idx, block in enumerate(blocks):
            name = block['channel_name']
            wrapper_url = block['wrapper_url']

            if is_skipped(name, wrapper_url):
                f.write(block['full'])
                continue

            result = results[idx]
            if result['valid']:
                f.write(block['extinf'])
                for option_line in result.get('output_option_lines', block['option_lines']):
                    f.write(option_line)

                if result['direct']:
                    f.write(result['direct'] + '\n')
                else:
                    f.write(block['wrapper_url'] + '\n')
            else:
                for line in block['full'].splitlines(keepends=True):
                    f.write('## ' + line)


# =============================================================================
# MAIN
# =============================================================================

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flatten_path = os.path.join(base_dir, 'Channels', 'Flatten.m3u8')
    main_path = os.path.join(base_dir, 'Main.m3u8')
    report_path = os.path.join(base_dir, 'validation-report.txt')

    run_time = datetime.now(MYT)
    run_time_str = run_time.strftime('%Y-%m-%d %H:%M:%S MYT')

    print(f"Parsing  : {flatten_path}")
    header, blocks = parse_flatten(flatten_path)
    total = len(blocks)
    print(f"Channels : {total}")
    print(f"Run time : {run_time_str}\n")

    results = [{} for _ in blocks]

    count_active = 0
    count_dead = 0
    count_skipped = 0
    count_direct = 0
    count_wrapper_kept = 0

    wrapper_session = requests.Session()
    wrapper_session.headers.update(DEFAULT_HEADERS)

    stream_session = requests.Session()
    stream_session.headers.update(DEFAULT_HEADERS)

    with open(report_path, 'w', encoding='utf-8') as report:
        report.write("# ============================================================\n")
        report.write("# IPTV Playlist Validation Report\n")
        report.write("# ============================================================\n")
        report.write(f"# Generated  : {run_time_str}\n")
        report.write("# Source     : Channels/Flatten.m3u8\n")
        report.write(f"# Total      : {total} channels\n")
        report.write(f"# Retry cfg  : MAX_RETRIES={MAX_RETRIES}, RETRY_DELAY={RETRY_DELAY}s\n")
        report.write("#\n")
        report.write("# Direct URL types (written to Main.m3u8 directly):\n")
        report.write(f"#   {', '.join(DIRECT_URL_TYPES)}\n")
        report.write("# Wrapper kept types (TV app follows wrapper chain):\n")
        report.write("#   hls_media\n")
        report.write("#\n")
        report.write("# Columns:\n")
        report.write("#   Channel    - tvg-name from Flatten.m3u8\n")
        report.write("#   Group      - group-title from Flatten.m3u8\n")
        report.write("#   Status     - active / dead / skipped\n")
        report.write("#   StreamType - hls_master / hls_media / dash / mp4 / etc\n")
        report.write("#   MainEntry  - wrapper_kept / direct_url / skipped / dead\n")
        report.write("#   URLsTested - position/total  e.g. 2/3 = 2nd of 3 worked\n")
        report.write("#   Attempts   - HTTP attempts before pass/fail\n")
        report.write("#\n")
        report.write("Channel,Group,Status,StreamType,MainEntry,URLsTested,Attempts\n")

        for i, block in enumerate(blocks):
            name = block['channel_name']
            group = block['group']
            wrapper_url = block['wrapper_url']

            print("=" * 60)
            print(f"[{i+1}/{total}] {name}  (group: {group})")

            if is_skipped(name, wrapper_url):
                results[i] = {'valid': True, 'direct': None}
                report.write(f"{name},{group},skipped,-,skipped,-,-,\n")
                count_skipped += 1
                print("Action  : ⏭️  Skipped (Mana-mana / tonton)")
                continue

            print(f"Wrapper : {wrapper_url}")

            try:
                wr = wrapper_session.get(wrapper_url, timeout=10)
                print(f"Wrapper HTTP: {wr.status_code}")

                if wr.status_code != 200:
                    results[i] = {'valid': False, 'direct': None}
                    report.write(
                        f"{name},{group},dead,"
                        f"wrapper_http_{wr.status_code},dead,0/0,1,\n"
                    )
                    count_dead += 1
                    print("Action  : ❌ Commented out (wrapper HTTP error)")
                    continue

                wrapper_content = wr.text

            except Exception as e:
                results[i] = {'valid': False, 'direct': None}
                report.write(f"{name},{group},dead,wrapper_exception,dead,0/0,1,\n")
                count_dead += 1
                print(f"Wrapper exception: {e}")
                print("Action  : ❌ Commented out (wrapper fetch failed)")
                continue

            wrapper_option_lines, wrapper_request_headers = extract_extvlcopt_headers(wrapper_content)
            merged_option_lines = merge_option_lines(block['option_lines'], wrapper_option_lines)

            inner_urls = extract_all_urls_from_wrapper(wrapper_content, wrapper_url)
            total_urls = len(inner_urls)

            if total_urls == 0:
                results[i] = {'valid': False, 'direct': None}
                report.write(f"{name},{group},dead,no_urls_in_wrapper,dead,0/0,0,\n")
                count_dead += 1
                print("Inner URLs : NONE FOUND")
                print("Action     : ❌ Commented out (no URLs in wrapper)")
                continue

            print(f"Inner URLs : {total_urls} found")

            channel_valid = False
            winning_url = ''
            winning_kind = ''
            winning_attempt = 0
            winning_position = 0

            for url_idx, inner_url in enumerate(inner_urls, start=1):
                print(f"  [{url_idx}/{total_urls}] {inner_url}")
                stream_kind, http_status, good, attempt = validate_stream(
                    stream_session,
                    inner_url,
                    extra_headers=wrapper_request_headers
                )
                print(f"  Result : {stream_kind} (HTTP {http_status}, attempt {attempt})")

                if good:
                    channel_valid = True
                    winning_url = inner_url
                    winning_kind = stream_kind
                    winning_attempt = attempt
                    winning_position = url_idx
                    print(f"  ✅ Working URL found at position {url_idx}/{total_urls}")
                    break
                else:
                    print(f"  ❌ URL {url_idx}/{total_urls} failed ({stream_kind}, HTTP {http_status})")

            if channel_valid:
                if winning_kind in DIRECT_URL_TYPES:
                    results[i] = {
                        'valid': True,
                        'direct': winning_url,
                        'output_option_lines': merged_option_lines,
                    }
                    main_entry = 'direct_url'
                    count_direct += 1
                    action_note = f"direct URL written to Main.m3u8 ({winning_kind})"
                else:
                    results[i] = {
                        'valid': True,
                        'direct': None,
                        'output_option_lines': merged_option_lines,
                    }
                    main_entry = 'wrapper_kept'
                    count_wrapper_kept += 1
                    action_note = f"wrapper kept ({winning_kind})"

                report.write(
                    f"{name},{group},active,{winning_kind},{main_entry},"
                    f"{winning_position}/{total_urls},{winning_attempt},{winning_url}\n"
                )
                count_active += 1
                print(
                    f"Action  : ✅ {action_note}  "
                    f"(URL {winning_position}/{total_urls}, attempt {winning_attempt})"
                )
            else:
                results[i] = {'valid': False, 'direct': None}
                report.write(
                    f"{name},{group},dead,all_urls_failed,dead,"
                    f"0/{total_urls},{MAX_RETRIES + 1},\n"
                )
                count_dead += 1
                print(f"Action  : ❌ Commented out (all {total_urls} URL(s) failed)")

        non_skipped = total - count_skipped
        success_pct = round(count_active / max(non_skipped, 1) * 100, 1)

        report.write("#\n")
        report.write("# ============================================================\n")
        report.write("# SUMMARY\n")
        report.write("# ============================================================\n")
        report.write(f"# Total        : {total}\n")
        report.write(f"# Active       : {count_active}\n")
        report.write(f"#   Wrapper kept : {count_wrapper_kept}  (hls_media — URL hidden)\n")
        report.write(f"#   Direct URL   : {count_direct}  (hls_master/dash/mp4 — URL exposed)\n")
        report.write(f"# Dead          : {count_dead}\n")
        report.write(f"# Skipped       : {count_skipped}\n")
        report.write(f"# Success       : {success_pct}%  (active / non-skipped)\n")
        report.write(f"# Run time      : {run_time_str}\n")
        report.write("# ============================================================\n")

    wrapper_session.close()
    stream_session.close()

    print("\n" + "=" * 60)
    print(f"Results  : {count_active} active | {count_dead} dead | {count_skipped} skipped")
    print(f"  Wrapper kept : {count_wrapper_kept}  |  Direct URL : {count_direct}")
    print(f"Success  : {success_pct}%")
    print("Writing Main.m3u8...")
    update_main_m3u8(main_path, header, blocks, results)
    print("Done.")


if __name__ == '__main__':
    main()
