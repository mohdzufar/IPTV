"""
validate_and_update.py - Validate non-Mana/tonton channels and update Main.m3u8.
Reads Flatten.m3u8 for validation, then modifies Main.m3u8 accordingly.
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
    """Fetch a URL with retries. Returns (data, final_url) or (None, None)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for attempt in range(MAX_RETRIES + 1):
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
                return raw, final_url
        except Exception as e:
            if attempt == MAX_RETRIES:
                return None, None
            time.sleep(RETRY_DELAY)
    return None, None

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
    # MP4 by extension or magic bytes
    if lower_url.endswith('.mp4') or lower_url.endswith('.m4v') or is_mp4_signature(data):
        return 'mp4'
    # DASH
    if lower_url.endswith('.mpd') or '/mpd' in lower_url:
        return 'dash'
    if text and text.strip().lower().startswith('<mpd'):
        return 'dash'
    # HLS master
    if text and '#EXT-X-STREAM-INF' in text:
        return 'hls_master'
    # HLS media
    if text and ('#EXT-X-TARGETDURATION' in text or '#EXT-X-MEDIA-SEQUENCE' in text):
        return 'hls_media'
    # Wrapper playlist (contains #EXTINF)
    if text and '#EXTINF' in text:
        return 'wrapper'
    return 'direct'

def extract_inner_url_from_wrapper(wrapper_url):
    """Fetch wrapper and return first http line, or None. Also returns raw data for classification."""
    raw, final_url = fetch_url(wrapper_url)
    if raw is None:
        return None, None, None
    text = raw.decode('utf-8', errors='ignore')
    lines = text.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith('http'):
            return line, raw, final_url
    # No inner URL? Then wrapper itself is the stream
    return wrapper_url, raw, final_url

def validate_stream(url):
    """
    Validate a stream URL (might be a wrapper or direct).
    Returns (success, resolved_url, stream_type, message).
    """
    # Check if it's an error page (HTML)
    raw, final_url = fetch_url(url)
    if raw is None:
        return False, url, None, "Fetch failed"
    text = raw.decode('utf-8', errors='ignore')
    if re.search(r'<html|<body|<!doctype', text, re.IGNORECASE):
        return False, url, None, "Error page (HTML)"
    kind = classify_content(raw, final_url)
    if kind in ('dash', 'mp4'):
        # For DASH/MP4, additional content checks
        if kind == 'dash':
            if '<mpd' not in text.lower():
                return False, final_url, kind, "DASH manifest missing <MPD>"
        if kind == 'mp4':
            if not is_mp4_signature(raw):
                return False, final_url, kind, "MP4 missing ftyp box"
        return True, final_url, kind, f"{kind.upper()} OK"
    elif kind in ('hls_master', 'hls_media', 'wrapper', 'direct'):
        # HLS streams or wrappers: we treat as HLS type for output rule
        return True, final_url, 'hls', f"Stream OK ({kind})"
    else:
        return True, final_url, 'hls', "Stream OK (assumed)"

def main():
    if not os.path.exists(FLATTEN_FILE):
        print(f"Source file {FLATTEN_FILE} not found.")
        return
    if not os.path.exists(MAIN_FILE):
        print(f"Target playlist {MAIN_FILE} not found. Run Step 1 first.")
        return

    # Read Flatten.m3u8 and extract channels
    with open(FLATTEN_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        flatten_lines = f.readlines()

    # Read current Main.m3u8 (already cleaned and with refreshed Mana URLs)
    with open(MAIN_FILE, 'r', encoding='utf-8') as f:
        main_lines = f.readlines()

    channels = []
    i = 0
    while i < len(flatten_lines):
        line = flatten_lines[i].strip()
        if line.startswith('#EXTINF:'):
            # Collect candidate URLs (wrapper URLs) and EXTVLCOPT tags
            candidates = []
            exvl_opts = []
            j = i + 1
            while j < len(flatten_lines):
                nl = flatten_lines[j].strip()
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
                    'original_url': candidates[0],   # we take first candidate
                    'candidates': candidates,
                    'skip': False
                })
            i = j
        else:
            i += 1

    report = []
    updated_main = main_lines[:]   # we will modify this list

    for ch in channels:
        # Determine if we should skip this channel (Mana-mana or tonton)
        if any(p.search(ch['original_url']) for p in SKIP_PATTERNS):
            ch['skip'] = True
            report.append((ch['extinf'], 'SKIPPED', 'Mana-mana or tonton'))
            continue

        print(f"Validating: {ch['extinf']}")
        wrapper_url = ch['original_url']
        # Step A: resolve inner stream from wrapper
        inner_url, raw, final_wrapper = extract_inner_url_from_wrapper(wrapper_url)
        if inner_url is None:
            # wrapper fetch failed
            success = False
            resolved = wrapper_url
            stype = None
            msg = "Failed to fetch wrapper"
        else:
            # Validate the inner stream
            success, resolved, stype, msg = validate_stream(inner_url)

        if success:
            # Apply the output rule
            if stype in ('dash', 'mp4'):
                # Put direct DASH/MP4 URL into Main.m3u8
                new_url = resolved
                action = 'direct'
            else:
                # HLS – keep original wrapper URL
                new_url = wrapper_url
                action = 'keep wrapper'
            # Update Main.m3u8: find the line with the original wrapper URL and replace it
            for idx, mline in enumerate(updated_main):
                if mline.strip() == wrapper_url:
                    updated_main[idx] = new_url + '\n'
                    break
            report.append((ch['extinf'], 'PASS', f"{msg} -> {action}"))
        else:
            # Comment out channel
            # Locate block in updated_main (the channel entry)
            # We'll search for extinf line and then the URL
            extinf_search = ch['extinf']
            for idx, mline in enumerate(updated_main):
                if mline.strip() == extinf_search:
                    # Comment the extinf line
                    if not mline.lstrip().startswith('##'):
                        updated_main[idx] = '## ' + mline.lstrip()
                    # Comment following EXTVLCOPT lines and the URL
                    j = idx + 1
                    while j < len(updated_main) and (updated_main[j].strip().startswith('#') or updated_main[j].strip().startswith('http')):
                        if not updated_main[j].lstrip().startswith('##'):
                            updated_main[j] = '## ' + updated_main[j].lstrip()
                        # Stop after we pass the URL
                        if updated_main[j].strip().startswith('## http'):
                            # We commented the URL, break
                            break
                        j += 1
                    break
            report.append((ch['extinf'], 'FAIL', msg))

    # Write report
    import csv
    with open(REPORT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Channel Name', 'Status', 'Reason'])
        for name, status, reason in report:
            # Extract a short channel name from EXTINF
            writer.writerow([name, status, reason])

    # Write updated Main.m3u8
    with open(MAIN_FILE, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(updated_main)

    print(f"Validation complete. Report: {REPORT_FILE}, Main.m3u8 updated.")

if __name__ == "__main__":
    main()
