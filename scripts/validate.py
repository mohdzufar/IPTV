#!/usr/bin/env python3
"""
validate.py - Validates IPTV stream URLs in an M3U8 playlist.

- Tests each channel's candidate URLs in order.
- Stops at the first working candidate and writes that URL to the output.
- Supports HLS (master/media), DASH (.mpd), MP4 (.mp4), and generic direct streams.
- DASH and MP4 are validated like HLS — no automatic rejection.
- Mana‑mana and Njoi wrapper URLs are unwrapped; on success the inner stream
  URL is placed directly in the validated Main.m3u8.
- Validated Main.m3u8 and a CSV report are produced.
"""

import urllib.request
import urllib.error
import ssl
import gzip
import io
import re
import os
import time
import csv
from urllib.parse import urljoin

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
PLAYLIST_FILE = "Main.m3u8"
REPORT_FILE = "validation-report.txt"
FETCH_TIMEOUT = 15                # total timeout for a single fetch
CANDIDATE_TIMEOUT = 15            # per-candidate timeout
MAX_RETRIES = 1                   # retries per fetch
RETRY_DELAY = 2                   # seconds between retries
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024  # 2 MB max per fetch
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# Mana‑mana internal URL pattern (used to decide candidate type)
MANA_PATTERN = re.compile(r"mana2\.my", re.IGNORECASE)
# Njoi internal URL pattern
NJOI_PATTERN = re.compile(r"njoi", re.IGNORECASE)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def is_mp4_content(data):
    """Check MP4 file signature: bytes 4-7 should spell 'ftyp'."""
    return len(data) >= 8 and data[4:8] == b'ftyp'

def classify_content(data, url):
    """
    Classify fetched content.
    Returns one of: 'hls_master', 'hls_media', 'dash', 'mp4', 'wrapper', 'direct', 'error'.
    """
    text = None
    try:
        text = data.decode('utf-8', errors='ignore')
    except:
        pass

    lower_url = url.lower()

    # 1. MP4 by file extension
    if lower_url.endswith('.mp4') or lower_url.endswith('.m4v'):
        return 'mp4'
    # MP4 by magic bytes (quick check)
    if is_mp4_content(data):
        return 'mp4'

    # 2. DASH by URL or content
    if lower_url.endswith('.mpd') or '/mpd' in lower_url:
        return 'dash'
    if text and text.strip().lower().startswith('<mpd'):
        return 'dash'

    # 3. HLS master
    if text and '#EXT-X-STREAM-INF' in text:
        return 'hls_master'

    # 4. HLS media
    if text and ('#EXT-X-TARGETDURATION' in text or '#EXT-X-MEDIA-SEQUENCE' in text
                 or '#EXT-X-KEY' in text or '#EXT-X-PROGRAM-DATE-TIME' in text):
        return 'hls_media'

    # 5. Simple wrapper playlist
    if text and '#EXTINF' in text:
        return 'wrapper'

    # 6. HTML error page
    if text and re.search(r'<html|<body|<!doctype', text, re.IGNORECASE):
        return 'error'

    # 7. Fallback: direct stream
    return 'direct'

def time_left(deadline):
    """Return remaining seconds until deadline, or 0 if expired."""
    if deadline is None:
        return float('inf')
    remaining = deadline - time.monotonic()
    return max(0, remaining)

def fetch_url(url, deadline, max_bytes=MAX_DOWNLOAD_BYTES):
    """
    Fetch URL with retries, respecting a deadline.
    Returns (data, success, final_url) where final_url is after redirects.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(MAX_RETRIES + 1):
        if time_left(deadline) <= 0:
            return b'', False, url  # timeout

        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=min(FETCH_TIMEOUT, time_left(deadline)), context=ctx) as resp:
                final_url = resp.geturl()
                content_encoding = resp.headers.get('Content-Encoding', '').lower()
                raw = b''
                while len(raw) < max_bytes:
                    chunk = resp.read(min(8192, max_bytes - len(raw)))
                    if not chunk:
                        break
                    raw += chunk
                # Decompress if needed
                if content_encoding == 'gzip':
                    raw = gzip.decompress(raw)
                elif content_encoding == 'deflate':
                    try:
                        raw = gzip.decompress(raw)
                    except:
                        pass
                return raw, True, final_url
        except urllib.error.HTTPError as e:
            if attempt == MAX_RETRIES:
                return b'', False, url
            time.sleep(RETRY_DELAY)
            continue
        except Exception as e:
            if attempt == MAX_RETRIES:
                return b'', False, url
            time.sleep(RETRY_DELAY)
            continue
    return b'', False, url

# ----------------------------------------------------------------------
# Stream validation
# ----------------------------------------------------------------------

def validate_hls_or_direct(url, deadline, extra_headers=None, depth=0):
    """
    Recursively validate a stream URL.
    extra_headers: dict of headers to add (from EXTVLCOPT).
    Returns (success, final_url, message).
    """
    if depth > 5:
        return False, url, "Max recursion depth"

    # Merge extra headers
    local_headers = HEADERS.copy()
    if extra_headers:
        local_headers.update(extra_headers)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers=local_headers)
        with urllib.request.urlopen(req, timeout=min(FETCH_TIMEOUT, time_left(deadline)), context=ctx) as resp:
            final_url = resp.geturl()
            content_encoding = resp.headers.get('Content-Encoding', '').lower()
            raw = b''
            while len(raw) < MAX_DOWNLOAD_BYTES:
                chunk = resp.read(8192)
                if not chunk:
                    break
                raw += chunk
            if content_encoding == 'gzip':
                raw = gzip.decompress(raw)
            elif content_encoding == 'deflate':
                try:
                    raw = gzip.decompress(raw)
                except:
                    pass

            # Classify
            kind = classify_content(raw, final_url)

            # Error page
            if kind == 'error':
                return False, final_url, "Error page (HTML) detected"

            # DASH
            if kind == 'dash':
                text = raw.decode('utf-8', errors='ignore').lower()
                if '<mpd' in text:
                    return True, final_url, "DASH manifest OK"
                else:
                    return False, final_url, "DASH manifest missing <MPD>"

            # MP4
            if kind == 'mp4':
                if is_mp4_content(raw):
                    return True, final_url, "MP4 file OK (ftyp signature found)"
                else:
                    return False, final_url, "MP4 file invalid (no ftyp box)"

            # HLS master
            if kind == 'hls_master':
                # Find the first variant stream URL
                text = raw.decode('utf-8', errors='ignore')
                lines = text.splitlines()
                variant_url = None
                for i, line in enumerate(lines):
                    if line.startswith('#EXT-X-STREAM-INF'):
                        # Next non-comment line is URL
                        for j in range(i+1, len(lines)):
                            candidate = lines[j].strip()
                            if candidate and not candidate.startswith('#'):
                                variant_url = candidate
                                break
                    if variant_url:
                        break
                if not variant_url:
                    # No variant found; treat as media playlist
                    return True, final_url, "HLS media playlist (no variant, assumed OK)"
                # Resolve relative URL
                if not variant_url.startswith('http'):
                    variant_url = urljoin(final_url, variant_url)
                # Recursively validate variant
                success, new_final, msg = validate_hls_or_direct(variant_url, deadline, extra_headers, depth+1)
                if success:
                    return True, new_final, f"HLS master -> variant OK: {msg}"
                else:
                    return False, final_url, f"HLS master variant failed: {msg}"

            # HLS media
            if kind == 'hls_media':
                return True, final_url, "HLS media playlist OK"

            # Wrapper or direct
            return True, final_url, f"Stream OK ({kind})"

    except urllib.error.HTTPError as e:
        return False, url, f"HTTP {e.code}"
    except Exception as e:
        return False, url, f"Fetch error: {str(e)}"

# ----------------------------------------------------------------------
# Candidate testers (Mana, Njoi, Generic)
# ----------------------------------------------------------------------

def validate_mana_candidate(wrapper_url, deadline):
    """
    Mana‑mana candidate: unwrap to inner stream, validate it.
    Returns (success, output_url, message). output_url will be the inner stream URL.
    """
    raw, ok, final_wrapper = fetch_url(wrapper_url, deadline)
    if not ok:
        return False, wrapper_url, "Failed to fetch Mana wrapper"

    text = raw.decode('utf-8', errors='ignore')
    lines = text.splitlines()
    inner_url = None
    extra_headers = {}

    # Parse wrapper looking for inner stream URL and EXTVLCOPT lines
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTVLCOPT:'):
            opt = line[len('#EXTVLCOPT:'):].strip()
            if '=' in opt:
                key, val = opt.split('=', 1)
                low_key = key.lower().replace('_', '-')
                if low_key in ('user-agent', 'http-user-agent'):
                    extra_headers['User-Agent'] = val
                elif low_key == 'referer':
                    extra_headers['Referer'] = val
                elif low_key == 'origin':
                    extra_headers['Origin'] = val
                else:
                    extra_headers[key] = val
        elif line.startswith('http'):
            inner_url = line

    if not inner_url:
        return False, wrapper_url, "No inner stream URL in Mana wrapper"

    # Validate inner stream
    success, final_inner, msg = validate_hls_or_direct(inner_url, deadline, extra_headers)
    if success:
        return True, final_inner, f"Mana inner OK: {msg}"
    else:
        return False, inner_url, f"Mana inner failed: {msg}"

def validate_njoi_candidate(wrapper_url, deadline):
    """
    Njoi candidate: unwrap to inner stream, validate it.
    Now returns the INNER stream URL on success (same as Mana‑mana).
    """
    raw, ok, final_wrapper = fetch_url(wrapper_url, deadline)
    if not ok:
        return False, wrapper_url, "Failed to fetch Njoi wrapper"

    text = raw.decode('utf-8', errors='ignore')
    lines = text.splitlines()
    inner_url = None

    for line in lines:
        line = line.strip()
        if line.startswith('http'):
            inner_url = line
            break

    if not inner_url:
        return False, wrapper_url, "No inner stream URL in Njoi wrapper"

    # Validate the inner stream directly (no DASH rejection now)
    success, final_inner, msg = validate_hls_or_direct(inner_url, deadline)
    if success:
        # Return the inner URL, not the wrapper
        return True, final_inner, f"Njoi inner OK: {msg}"
    else:
        return False, inner_url, f"Njoi inner failed: {msg}"

def validate_generic_candidate(url, deadline):
    """Simple direct validation."""
    success, final_url, msg = validate_hls_or_direct(url, deadline)
    if success:
        return True, final_url, msg
    else:
        return False, url, msg

def test_candidate(url, deadline):
    """
    Dispatch candidate to the appropriate tester based on URL patterns.
    Returns (success, output_url, message).
    """
    if MANA_PATTERN.search(url):
        return validate_mana_candidate(url, deadline)
    elif NJOI_PATTERN.search(url):
        return validate_njoi_candidate(url, deadline)
    else:
        return validate_generic_candidate(url, deadline)

# ----------------------------------------------------------------------
# Channel processing
# ----------------------------------------------------------------------

def process_channel(channel_name, candidates, deadline):
    """
    Test candidates in order, stop at first success.
    Returns (success, output_url, message, all_fail_reasons).
    """
    reasons = []
    for idx, cand_url in enumerate(candidates, 1):
        if time_left(deadline) <= 0:
            reasons.append("Timeout reached")
            return False, cand_url, "Timeout", reasons

        success, out_url, msg = test_candidate(cand_url, deadline)
        if success:
            return True, out_url, msg, reasons + [f"Candidate {idx}: OK ({msg})"]
        else:
            reasons.append(f"Candidate {idx}: {msg}")

    return False, candidates[0] if candidates else '', "All candidates failed", reasons

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    if not os.path.exists(PLAYLIST_FILE):
        print(f"Playlist {PLAYLIST_FILE} not found.")
        return

    with open(PLAYLIST_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    output_lines = []
    report_rows = []
    channel_count = 0
    total_candidates = 0

    # Global deadline: total script can run up to 45 minutes (GitHub limit)
    script_deadline = time.monotonic() + 45 * 60

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            channel_name = line[len('#EXTINF:'):].strip()
            # Collect following URLs (candidates) and any EXTVLCOPT tags
            candidates = []
            j = i + 1
            exvl_opts = []
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line.startswith('#EXTVLCOPT:'):
                    exvl_opts.append(next_line)
                    j += 1
                elif next_line.startswith('http'):
                    candidates.append(next_line)
                    j += 1
                elif next_line.startswith('#'):
                    # Other tags (e.g., #EXTGRP) we keep but don't treat as candidates
                    exvl_opts.append(next_line)
                    j += 1
                else:
                    break
            if not candidates:
                # Keep original lines (no URL to test)
                output_lines.extend(lines[i:j])
                i = j
                continue

            channel_count += 1
            total_candidates += len(candidates)

            # Per-channel deadline
            chan_deadline = min(script_deadline, time.monotonic() + CANDIDATE_TIMEOUT * len(candidates))

            print(f"[{time.strftime('%H:%M:%S')}] Ch {channel_count}: Testing: {channel_name}")

            success, out_url, msg, reasons = process_channel(channel_name, candidates, chan_deadline)

            # Write report
            report_rows.append({
                'Channel Name': channel_name,
                'Status': 'PASS' if success else 'FAIL',
                'Reason': msg + ' | ' + ' ; '.join(reasons[-3:])  # last 3 reasons for brevity
            })

            if success:
                # Output: #EXTINF line, any EXTVLCOPT lines, then the working URL
                output_lines.append(line + '\n')
                for opt in exvl_opts:
                    output_lines.append(opt + '\n')
                output_lines.append(out_url + '\n')
            else:
                # Comment out the whole block
                output_lines.append(f"## {line}\n")
                for opt in exvl_opts:
                    output_lines.append(f"## {opt}\n")
                for cand in candidates:
                    output_lines.append(f"## {cand}\n")
            i = j
        else:
            output_lines.append(lines[i])
            i += 1

    # Write validated M3U8
    with open(PLAYLIST_FILE, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)

    # Write CSV report
    with open(REPORT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['Channel Name', 'Status', 'Reason'])
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"\nDone. {channel_count} channels, {total_candidates} candidates tested.")
    print(f"Output written to {PLAYLIST_FILE} and {REPORT_FILE}")

if __name__ == "__main__":
    main()
