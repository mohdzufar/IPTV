#!/usr/bin/env python3
"""
validate_and_update.py
Reads Flatten.m3u8 (list of per‑channel wrapper M3U8 files),
fetches each wrapper, extracts #EXTVLCOPT directives and stream URLs,
validates streams using those directives, and produces:
  - Main.m3u8 (final playlist with proxy URLs)
  - validation-report.txt (per‑channel validation status)
"""

import os
import re
import requests
import sys
from urllib.parse import quote

# ----------------------------------------------------------------------
# Configuration – set via environment or hardcoded defaults (safe to commit)
# ----------------------------------------------------------------------
INPUT_PLAYLIST = os.environ.get("INPUT_PLAYLIST", "Channels/Flatten.m3u8")
OUTPUT_PLAYLIST = os.environ.get("OUTPUT_PLAYLIST", "Main.m3u8")
REPORT_FILE = os.environ.get("REPORT_FILE", "validation-report.txt")

WRAPPER_BASE = os.environ.get("WRAPPER_BASE_URL", "http://your-iptv-server.com:8080")
WRAPPER_USER = os.environ.get("WRAPPER_USER", "your-user")
WRAPPER_PASS = os.environ.get("WRAPPER_PASS", "your-pass")

# Timeout for fetching wrapper playlists and testing streams
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "15"))

# Fallback User‑Agent if none is provided by EXTVLCOPT
FALLBACK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# ----------------------------------------------------------------------
# Helper: parse a line into (tag, value) for #EXTVLCOPT and #EXTINF
# ----------------------------------------------------------------------
def parse_extvlcopt(line: str):
    """Parse a line like '#EXTVLCOPT:http-user-agent=...' into (key, value)."""
    line = line.strip()
    if not line.startswith("#EXTVLCOPT:"):
        return None, None
    opt = line[len("#EXTVLCOPT:"):].strip()
    if '=' in opt:
        key, value = opt.split('=', 1)
        return key.strip().lower(), value.strip()
    else:
        return opt.strip().lower(), None

def parse_http_header_option(value: str):
    """Parse http-header value like 'Header-Name: Header-Value'."""
    if ':' in value:
        name, val = value.split(':', 1)
        return name.strip(), val.strip()
    return None, None

# ----------------------------------------------------------------------
# Core: validate a single stream URL using a pre‑built headers dict
# ----------------------------------------------------------------------
def test_stream(url: str, headers: dict, timeout: int = REQUEST_TIMEOUT):
    """
    Returns (status_code, content_type, error_message)
    status_code 0 means a network/other error.
    """
    try:
        r = requests.get(url, headers=headers, timeout=timeout, stream=True)
        content_type = r.headers.get('Content-Type', '').lower()
        if r.status_code == 200:
            if 'html' in content_type:
                return 200, content_type, "Server returned HTML instead of stream"
            return 200, content_type, None
        else:
            return r.status_code, content_type, f"HTTP {r.status_code}"
    except Exception as e:
        return 0, None, str(e)

# ----------------------------------------------------------------------
# Parse a wrapper M3U8 file content and extract stream URLs + headers context
# ----------------------------------------------------------------------
def parse_wrapper_m3u8(m3u8_text: str):
    """Returns a list of tuples: (stream_url, headers_dict)."""
    streams = []
    current_headers = {"User-Agent": FALLBACK_UA}

    for raw_line in m3u8_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXTVLCOPT:"):
            key, value = parse_extvlcopt(line)
            if key is None:
                continue
            if key == "http-user-agent":
                current_headers["User-Agent"] = value
            elif key == "http-referrer":
                current_headers["Referer"] = value
            elif key == "http-header":
                name, val = parse_http_header_option(value)
                if name and val:
                    current_headers[name] = val
            continue

        if line.startswith("#"):
            continue

        # It's a URL
        streams.append((line, dict(current_headers)))

    return streams

# ----------------------------------------------------------------------
# Main validation routine
# ----------------------------------------------------------------------
def main():
    if not os.path.exists(INPUT_PLAYLIST):
        print(f"ERROR: Input playlist '{INPUT_PLAYLIST}' not found.")
        sys.exit(1)

    with open(INPUT_PLAYLIST, 'r', encoding='utf-8') as f:
        main_playlist = f.read()

    entry_pattern = re.compile(r'^#EXTINF:(.*)$\n^(https?://\S+)', re.MULTILINE)
    entries = entry_pattern.findall(main_playlist)

    if not entries:
        print("No valid entries found in main playlist.")
        sys.exit(1)

    valid_lines = ["#EXTM3U"]
    report_lines = ["Validation Report", "=" * 50, ""]

    for idx, (extinf_line, wrapper_url) in enumerate(entries, start=1):
        attrs = {}
        for part in extinf_line.strip().split():
            if '=' in part:
                k, v = part.split('=', 1)
                v = v.strip('"')
                attrs[k] = v

        channel_id = attrs.get("tvg-id", f"channel-{idx}")
        channel_name = attrs.get("tvg-name", "Unknown")
        logo = attrs.get("tvg-logo", "")
        group = attrs.get("group-title", "Undefined")

        print(f"[{idx}/{len(entries)}] {channel_name}  (group: {group})")
        print(f"Wrapper : {wrapper_url}")

        try:
            wrapper_resp = requests.get(wrapper_url, headers={"User-Agent": FALLBACK_UA}, timeout=REQUEST_TIMEOUT)
            if wrapper_resp.status_code != 200:
                print(f"  [FAIL] Failed to fetch wrapper M3U8 (HTTP {wrapper_resp.status_code})")
                report_lines.append(f"[INVALID] {wrapper_url} - {channel_name} (wrapper not reachable)")
                continue
            wrapper_text = wrapper_resp.text
        except Exception as e:
            print(f"  [FAIL] Error fetching wrapper M3U8: {e}")
            report_lines.append(f"[INVALID] {wrapper_url} - {channel_name} (wrapper fetch error: {e})")
            continue

        streams = parse_wrapper_m3u8(wrapper_text)
        if not streams:
            print("  [FAIL] No stream URLs found in wrapper")
            report_lines.append(f"[INVALID] {wrapper_url} - {channel_name} (no streams)")
            continue

        print(f"  Inner URLs : {len(streams)} found")

        channel_valid = False
        best_stream = None

        for si, (stream_url, headers) in enumerate(streams, start=1):
            print(f"  [{si}/{len(streams)}] {stream_url}")
            status, ct, err = test_stream(stream_url, headers)
            if status == 200 and err is None:
                print(f"  [OK] Valid (Content-Type: {ct})")
                channel_valid = True
                if best_stream is None:
                    best_stream = stream_url
            else:
                if status == 200:
                    print(f"  [FAIL] 200 but HTML error page")
                elif status == 0:
                    print(f"  [FAIL] Error: {err}")
                else:
                    print(f"  [FAIL] HTTP {status} ({err})")

        if channel_valid and best_stream:
            proxy_url = f"{WRAPPER_BASE}/live/{WRAPPER_USER}/{WRAPPER_PASS}/{quote(channel_id)}"
            extinf = f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{channel_name}" tvg-logo="{logo}" group-title="{group}",{channel_name}'
            valid_lines.append(extinf)
            valid_lines.append(proxy_url)
            report_lines.append(f"[VALID] {proxy_url} - {channel_name} (used {best_stream})")
            print(f"  --> Added as VALID with proxy URL")
        else:
            report_lines.append(f"[INVALID] {wrapper_url} - {channel_name} (all inner streams failed)")
            print(f"  --> Marked INVALID")

    with open(OUTPUT_PLAYLIST, 'w', encoding='utf-8') as f:
        f.write("\n".join(valid_lines) + "\n")

    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(report_lines) + "\n")

    print(f"\nDone. Valid entries written to {OUTPUT_PLAYLIST}, report to {REPORT_FILE}")

if __name__ == "__main__":
    main()
