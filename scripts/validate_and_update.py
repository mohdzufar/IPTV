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
# (TV apps cannot follow wrapper chain for these)
DIRECT_URL_TYPES = ('hls_master', 'dash', 'mp4')

# Malaysia timezone (UTC+8)
MYT = timezone(timedelta(hours=8))


# =============================================================================
# URL EXTRACTION
# =============================================================================

def extract_all_urls_from_wrapper(wrapper_content, base_url):
    """Extract ALL stream URLs from a wrapper .m3u8 file, in order.
    Skips comment lines and directive lines starting with #.
    """
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

def validate_stream(stream_session, url):
    """Fetch a stream URL and classify it. Retries up to MAX_RETRIES times.
    Uses a shared session for connection reuse (avoids DNS exhaustion).
    Returns (kind, http_status, good, attempt_number).
    """
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
            # binary and wrapper are not directly playable
            good = kind not in ('invalid', 'html', 'empty_ok', 'binary', 'wrapper')

            if good:
                return kind, http_status, True, attempt

            # Hard HTTP failures — no point retrying
            if http_status in (401, 403, 404, 410):
                return kind, http_status, False, attempt

            # Soft failure — retry if attempts remain
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
    """Parse Flatten.m3u8 and return (header_line, list_of_blocks)."""
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
        name_match   = re.search(r'tvg-name="([^"]*)"', extinf)
        channel_name = name_match.group(1) if name_match else 'Unknown'
        group_match  = re.search(r'group-title="([^"]*)"', extinf)
        group        = group_match.group(1) if group_match else ''
        blocks.append((m.group(0), extinf, url_lines, channel_name, group))
    return header, blocks


# =============================================================================
# MAIN.M3U8 WRITER
# =============================================================================

def update_main_m3u8(main_path, header, blocks, results):
    """Write Main.m3u8 using the original EPG header line."""
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write((header or '#EXTM3U') + '\n')
        for idx, (full, extinf, url, name, group) in enumerate(blocks):
            if is_skipped(name, url):
                f.write(full)
                continue
            result = results[idx]
            if result['valid']:
                if result['direct']:
                    # DASH / MP4 / HLS master — write direct URL
                    f.write(extinf)
                    f.write(result['direct'] + '\n')
                else:
                    # HLS media — keep wrapper URL
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

    results = [{} for _ in blocks]

    count_active       = 0
    count_dead         = 0
    count_skipped      = 0
    count_direct       = 0
    count_wrapper_kept = 0

    # -------------------------------------------------------------------------
    # Two persistent sessions — one DNS lookup each for the entire run.
    # Prevents Windows DNS exhaustion when validating 150+ channels.
    # -------------------------------------------------------------------------
    wrapper_session = requests.Session()
    wrapper_session.headers.update(DEFAULT_HEADERS)

    stream_session = requests.Session()
    stream_session.headers.update(DEFAULT_HEADERS)

    with open(report_path, 'w', encoding='utf-8') as report:

        # Report header
        report.write(f"# ============================================================\n")
        report.write(f"# IPTV Playlist Validation Report\n")
        report.write(f"# ============================================================\n")
        report.write(f"# Generated  : {run_time_str}\n")
        report.write(f"# Source     : Channels/Flatten.m3u8\n")
        report.write(f"# Total      : {total} channels\n")
        report.write(f"# Retry cfg  : MAX_RETRIES={MAX_RETRIES}, "
                     f"RETRY_DELAY={RETRY_DELAY}s\n")
        report.write(f"#\n")
        report.write(f"# Columns:\n")
        report.write(f"#   Channel    - tvg-name from Flatten.m3u8\n")
        report.write(f"#   Group      - group-title from Flatten.m3u8\n")
        report.write(f"#   Status     - active / dead / skipped\n")
        report.write(f"#   StreamType - hls_master / hls_media / dash / mp4 / etc\n")
        report.write(f"#   MainEntry  - wrapper_kept / direct_url / skipped / dead\n")
        report.write(f"#   URLsTested - position/total  e.g. 2/3 = 2nd of 3 worked\n")
        report.write(f"#   Attempts   - HTTP attempts before pass/fail\n")
        report.write(f"#\n")
        report.write(f"Channel,Group,Status,StreamType,MainEntry,URLsTested,Attempts\n")

        for i, (full, extinf, url, name, group) in enumerate(blocks):
            print("=" * 60)
            print(f"[{i+1}/{total}] {name}  (group: {group})")

            # ------------------------------------------------------------------
            # Skipped channels (Mana-mana, tonton)
            # ------------------------------------------------------------------
            if is_skipped(name, url):
                results[i] = {'valid': True, 'direct': None}
                report.write(f"{name},{group},skipped,-,skipped,-,-\n")
                count_skipped += 1
                print("Action  : ⏭️  Skipped (Mana-mana / tonton)")
                continue

            print(f"Wrapper : {url.strip()}")

            # ------------------------------------------------------------------
            # Step 1: Fetch wrapper file
            # ------------------------------------------------------------------
            try:
                wr = wrapper_session.get(url.strip(), timeout=10)
                print(f"Wrapper HTTP: {wr.status_code}")

                if wr.status_code != 200:
                    results[i] = {'valid': False, 'direct': None}
                    report.write(
                        f"{name},{group},dead,"
                        f"wrapper_http_{wr.status_code},dead,0/0,1\n"
                    )
                    count_dead += 1
                    print("Action  : ❌ Commented out (wrapper HTTP error)")
                    continue

                wrapper_content = wr.text

            except Exception as e:
                results[i] = {'valid': False, 'direct': None}
                report.write(
                    f"{name},{group},dead,wrapper_exception,dead,0/0,1\n"
                )
                count_dead += 1
                print(f"Wrapper exception: {e}")
                print("Action  : ❌ Commented out (wrapper fetch failed)")
                continue

            # ------------------------------------------------------------------
            # Step 2: Extract all URLs from wrapper
            # ------------------------------------------------------------------
            inner_urls = extract_all_urls_from_wrapper(
                wrapper_content, url.strip()
            )
            total_urls = len(inner_urls)

            if total_urls == 0:
                results[i] = {'valid': False, 'direct': None}
                report.write(
                    f"{name},{group},dead,no_urls_in_wrapper,dead,0/0,0\n"
                )
                count_dead += 1
                print("Inner URLs : NONE FOUND")
                print("Action     : ❌ Commented out (no URLs in wrapper)")
                continue

            print(f"Inner URLs : {total_urls} found")

            # ------------------------------------------------------------------
            # Step 3: Try each URL until one works
            # ------------------------------------------------------------------
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
                    print(f"  ✅ Working URL found at position {url_idx}/{total_urls}")
                    break
                else:
                    print(f"  ❌ URL {url_idx}/{total_urls} failed "
                          f"({stream_kind}, HTTP {http_status})")

            # ------------------------------------------------------------------
            # Step 4: Record result
            # ------------------------------------------------------------------
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

                report.write(
                    f"{name},{group},active,{winning_kind},{main_entry},"
                    f"{winning_position}/{total_urls},{winning_attempt}\n"
                )
                count_active += 1
                print(
                    f"Action  : ✅ {action_note}  "
                    f"(URL {winning_position}/{total_urls}, "
                    f"attempt {winning_attempt})"
                )
            else:
                results[i] = {'valid': False, 'direct': None}
                report.write(
                    f"{name},{group},dead,all_urls_failed,dead,"
                    f"0/{total_urls},{MAX_RETRIES + 1}\n"
                )
                count_dead += 1
                print(f"Action  : ❌ Commented out "
                      f"(all {total_urls} URL(s) failed)")

        # Summary footer
        non_skipped = total - count_skipped
        success_pct = round(count_active / max(non_skipped, 1) * 100, 1)

        report.write(f"#\n")
        report.write(f"# ============================================================\n")
        report.write(f"# SUMMARY\n")
        report.write(f"# ============================================================\n")
        report.write(f"# Total          : {total}\n")
        report.write(f"# Active         : {count_active}\n")
        report.write(f"#   Wrapper kept : {count_wrapper_kept}  (URL hidden)\n")
        report.write(f"#   Direct URL   : {count_direct}  (hls_master/dash/mp4)\n")
        report.write(f"# Dead           : {count_dead}\n")
        report.write(f"# Skipped        : {count_skipped}\n")
        report.write(f"# Success        : {success_pct}%  (active / non-skipped)\n")
        report.write(f"# Run time       : {run_time_str}\n")
        report.write(f"# ============================================================\n")

    # Close sessions cleanly
    wrapper_session.close()
    stream_session.close()

    print("\n" + "=" * 60)
    print(f"Results  : {count_active} active | {count_dead} dead | "
          f"{count_skipped} skipped")
    print(f"  Wrapper kept : {count_wrapper_kept}  |  "
          f"Direct URL : {count_direct}")
    print(f"Success  : {success_pct}%")
    print("Writing Main.m3u8...")
    update_main_m3u8(main_path, header, blocks, results)
    print("Done.")


if __name__ == '__main__':
    main()
