"""
validate_and_update.py - Validate non-Mana/tonton channels and update Main.m3u8.
Reads Flatten.m3u8 for validation, then modifies Main.m3u8 accordingly.
Enhanced logging for each step.
"""
import urllib.request
import urllib.error
import ssl
import gzip
import re
import os
import sys
import time

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Configuration
FLATTEN_FILE = "Channels/Flatten.m3u8"
MAIN_FILE = "Main.m3u8"
REPORT_FILE = "validation-report.txt"
SKIP_PATTERNS = [re.compile(r"Mana-mana", re.IGNORECASE), re.compile(r"tonton", re.IGNORECASE)]
TIMEOUT = 15
MAX_RETRIES = 1
RETRY_DELAY = 2
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024   # 2 MB
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

def fetch_url(url, max_bytes=MAX_DOWNLOAD_BYTES):
    """Fetch a URL with retries. Returns (data, final_url, elapsed) or (None, None, elapsed)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for attempt in range(MAX_RETRIES + 1):
        start = time.time()
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
                final_url = resp.geturl()
                raw = b''
                while len(raw) < max_bytes:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    raw += chunk
                ce = resp.headers.get('Content-Encoding', '').lower()
                if ce == 'gzip':
                    raw = gzip.decompress(raw)
                elif ce == 'deflate':
                    try:
                        raw = gzip.decompress(raw)
                    except:
                        pass
                elapsed = round(time.time() - start, 1)
                return raw, final_url, elapsed, resp.status, resp.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            elapsed = round(time.time() - start, 1)
            if attempt == MAX_RETRIES:
                return None, None, elapsed, e.code, ''
            time.sleep(RETRY_DELAY)
        except Exception as e:
            elapsed = round(time.time() - start, 1)
            if attempt == MAX_RETRIES:
                return None, None, elapsed, None, ''
            time.sleep(RETRY_DELAY)
    return None, None, 0, None, ''

def is_mp4_signature(data):
    return len(data) >= 8 and data[4:8] == b'ftyp'

def classify_content(data, url):
    """Classify stream content. Returns one of: 'dash', 'mp4', 'hls_master', 'hls_media', 'wrapper', 'direct'."""
    text = None
    try:
        text = data.decode('utf-8', errors='ignore')
    except:
        pass
    lower_url = url.lower()
    if lower_url.endswith('.mp4') or lower_url.endswith('.m4v') or is_mp4_signature(data):
        return 'mp4'
    if lower_url.endswith('.mpd') or '/mpd' in lower_url:
        return 'dash'
    if text and text.strip().lower().startswith('<mpd'):
        return 'dash'
    if text and '#EXT-X-STREAM-INF' in text:
        return 'hls_master'
    if text and ('#EXT-X-TARGETDURATION' in text or '#EXT-X-MEDIA-SEQUENCE' in text):
        return 'hls_media'
    if text and '#EXTINF' in text:
        return 'wrapper'
    return 'direct'

def extract_inner_url_from_wrapper(wrapper_url):
    """Fetch wrapper and return first http line, or None. Also returns raw data for classification."""
    raw, final_url, elapsed, status, content_type = fetch_url(wrapper_url)
    if raw is None:
        return None, None, None, elapsed, status
    text = raw.decode('utf-8', errors='ignore')
    lines = text.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith('http'):
            return line, raw, final_url, elapsed, status
    return wrapper_url, raw, final_url, elapsed, status

def validate_stream(url):
    """
    Validate a stream URL. Returns (success, resolved_url, stream_type, message, elapsed, status, content_type).
    """
    raw, final_url, elapsed, status, content_type = fetch_url(url)
    if raw is None:
        return False, url, None, f"Fetch failed (HTTP {status})" if status else "Fetch failed", elapsed, status, content_type
    text = raw.decode('utf-8', errors='ignore')
    if re.search(r'<html|<body|<!doctype', text, re.IGNORECASE):
        return False, url, None, "Error page (HTML)", elapsed, status, content_type
    kind = classify_content(raw, final_url)
    if kind in ('dash', 'mp4'):
        if kind == 'dash':
            if '<mpd' not in text.lower():
                return False, final_url, kind, "DASH manifest missing <MPD>", elapsed, status, content_type
        if kind == 'mp4':
            if not is_mp4_signature(raw):
                return False, final_url, kind, "MP4 missing ftyp box", elapsed, status, content_type
        return True, final_url, kind, f"{kind.upper()} OK", elapsed, status, content_type
    elif kind in ('hls_master', 'hls_media', 'wrapper', 'direct'):
        return True, final_url, 'hls', f"Stream OK ({kind})", elapsed, status, content_type
    else:
        return True, final_url, 'hls', "Stream OK (assumed)", elapsed, status, content_type

def main():
    if not os.path.exists(FLATTEN_FILE):
        print(f"Source file {FLATTEN_FILE} not found.")
        return
    if not os.path.exists(MAIN_FILE):
        print(f"Target playlist {MAIN_FILE} not found. Run Step 1 first.")
        return

    with open(FLATTEN_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        flatten_lines = f.readlines()
    with open(MAIN_FILE, 'r', encoding='utf-8') as f:
        main_lines = f.readlines()

    channels = []
    i = 0
    while i < len(flatten_lines):
        line = flatten_lines[i].strip()
        if line.startswith('#EXTINF:'):
            candidates = []
            exvl_opts = []
            j = i + 1
            while j < len(flatten_lines):
                nl = flatten_lines[j].strip()
                if nl.startswith('#EXTINF:') or nl == '':
                    break
                if nl.startswith('#EXTVLCOPT:'):
                    exvl_opts.append(nl)
                    j += 1
                elif nl.startswith('http'):
                    candidates.append(nl)
                    j += 1
                elif nl.startswith('#'):
                    exvl_opts.append(nl)
                    j += 1
                else:
                    break
            if candidates:
                channels.append({
                    'extinf': line,
                    'exvl_opts': exvl_opts,
                    'original_url': candidates[0],
                    'candidates': candidates,
                })
            i = j
        else:
            i += 1

    total = len(channels)
    report = []
    updated_main = main_lines[:]

    print(f"Validation started: {total} channels to process.\n")

    for idx, ch in enumerate(channels, 1):
        name_match = re.search(r',\s*(.*)', ch['extinf'])
        short_name = name_match.group(1) if name_match else ch['extinf'][:60]

        # Skip patterns
        if any(p.search(ch['original_url']) for p in SKIP_PATTERNS):
            print(f"[{idx:03d}/{total}] SKIP   | {short_name:<30} (Mana-mana or tonton)")
            report.append((ch['extinf'], 'SKIPPED', 'Mana-mana or tonton'))
            continue

        wrapper_url = ch['original_url']
        print(f"[{idx:03d}/{total}] TEST   | {short_name:<30}")
        print(f"           Wrapper : {wrapper_url}")

        # Fetch wrapper
        print(f"           ↳ Fetching wrapper...")
        inner_url, raw, final_wrapper, wrapper_elapsed, wrapper_status = extract_inner_url_from_wrapper(wrapper_url)

        if inner_url is None:
            print(f"           ↳ Failed to fetch wrapper: HTTP {wrapper_status}")
            print(f"           ✘ Validation failed: Failed to fetch wrapper")
            print(f"           Action: comment out channel")
            report.append((ch['extinf'], 'FAIL', f"Failed to fetch wrapper (HTTP {wrapper_status})"))
            # Comment out in Main.m3u8
            extinf_search = ch['extinf']
            for midx, mline in enumerate(updated_main):
                if mline.strip() == extinf_search:
                    if not mline.lstrip().startswith('##'):
                        updated_main[midx] = '## ' + mline.lstrip()
                    j = midx + 1
                    while j < len(updated_main) and (updated_main[j].strip().startswith('#') or updated_main[j].strip().startswith('http')):
                        if not updated_main[j].lstrip().startswith('##'):
                            updated_main[j] = '## ' + updated_main[j].lstrip()
                        if updated_main[j].strip().startswith('## http'):
                            break
                        j += 1
                    break
            continue

        print(f"           ↳ Wrapper fetched (200, {wrapper_elapsed}s)")
        print(f"           ↳ Inner URL found: {inner_url}")

        # Validate inner stream
        print(f"           ↳ Validating inner stream...")
        print(f"             ↳ Fetching inner stream...")
        success, resolved, stype, msg, elapsed, status, content_type = validate_stream(inner_url)

        if not success:
            print(f"             ↳ Response: {status}, {content_type} ({elapsed}s)")
            print(f"           ✘ Validation failed: {msg}")
            print(f"           Action: comment out channel")
            report.append((ch['extinf'], 'FAIL', msg))
            # Comment out in Main.m3u8
            extinf_search = ch['extinf']
            for midx, mline in enumerate(updated_main):
                if mline.strip() == extinf_search:
                    if not mline.lstrip().startswith('##'):
                        updated_main[midx] = '## ' + mline.lstrip()
                    j = midx + 1
                    while j < len(updated_main) and (updated_main[j].strip().startswith('#') or updated_main[j].strip().startswith('http')):
                        if not updated_main[j].lstrip().startswith('##'):
                            updated_main[j] = '## ' + updated_main[j].lstrip()
                        if updated_main[j].strip().startswith('## http'):
                            break
                        j += 1
                    break
        else:
            print(f"             ↳ Response: 200, {content_type} ({elapsed}s)")
            print(f"             ↳ Classification: {stype}")
            print(f"           ✔ Stream valid -> type = {stype}")

            if stype in ('dash', 'mp4'):
                new_url = resolved
                action = 'direct URL'
                print(f"           Action: replace with direct URL")
                print(f"           New URL: {new_url}")
            else:
                new_url = wrapper_url
                action = 'keep wrapper'
                print(f"           Action: {action}")

            # Replace in Main.m3u8
            for midx, mline in enumerate(updated_main):
                if mline.strip() == wrapper_url:
                    updated_main[midx] = new_url + '\n'
                    break
            report.append((ch['extinf'], 'PASS', f"{msg} -> {action}"))

    # Write CSV report
    import csv
    with open(REPORT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Channel Name', 'Status', 'Reason'])
        for name, status, reason in report:
            writer.writerow([name, status, reason])

    with open(MAIN_FILE, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(updated_main)

    pass_count = sum(1 for _, status, _ in report if status == 'PASS')
    fail_count = sum(1 for _, status, _ in report if status == 'FAIL')
    skip_count = sum(1 for _, status, _ in report if status == 'SKIPPED')
    print(f"\nValidation complete: {pass_count} passed, {fail_count} failed, {skip_count} skipped.")
    print(f"Report -> {REPORT_FILE}, Main.m3u8 updated.")

if __name__ == "__main__":
    main()
