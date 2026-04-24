#!/usr/bin/env python3
"""
IPTV Playlist Validator and Mana-mana Unwrapper.
- Reads Main.m3u8 (pre-flattened, one URL per channel).
- Tests each channel's candidate URL.
- For Mana-mana channels: fetches the inner .m3u8, extracts the stream URL
  and #EXTVLCOPT headers, tests the stream with those headers, and
  replaces the channel entry with the unwrapped URL + headers.
- For non-Mana-mana: shallow test (fetch, check it's a valid playlist).
- Comments out any channel that fails all candidates (only one candidate
  by default, but can handle multiple).
- Produces validated Main.m3u8 and a validation-report.txt.
"""

import urllib.request
import urllib.error
import sys
import time
import io
from pathlib import Path
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Main.m3u8"
OUTPUT_FILE = "Main.m3u8"
REPORT_FILE = "validation-report.txt"

# Timeouts
TIMEOUT = 25                 # per request socket timeout
CANDIDATE_TIMEOUT = 30       # hard wall-clock timeout for testing one candidate

MAX_RETRIES = 1
RETRY_DELAY = 2
MAX_READ_BYTES = 2 * 1024 * 1024   # 2 MB

PARALLEL_WORKERS = 2         # max concurrent candidate tests inside one channel

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

MEDIA_PLAYLIST_TAGS = (
    "#EXT-X-TARGETDURATION",
    "#EXT-X-MEDIA-SEQUENCE",
    "#EXT-X-ENDLIST",
    "#EXT-X-KEY",
    "#EXT-X-MAP",
    "#EXT-X-PART",
    "#EXT-X-DISCONTINUITY",
)

# Mapping from EXTVLCOPT header keys to real HTTP header names
EXTVLCOPT_HEADER_MAP = {
    "http-user-agent": "User-Agent",
    "http-referrer":   "Referer",
    # add more if needed in the future
}

# -------------------------------------------------------------------
# UTILITY FUNCTIONS
# -------------------------------------------------------------------
def log_progress(channel_num, total_channels, message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] Ch {channel_num}/{total_channels}: {message}")


def log_detail(message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"      [{timestamp}] {message}")


def safe_urljoin(base, url):
    if url.startswith("//"):
        parsed = urlparse(base)
        return f"{parsed.scheme}:{url}"
    return urljoin(base, url)


def time_left(deadline):
    return deadline - time.monotonic()


def decode_text(data, limit=None):
    if limit is None:
        return data.decode("utf-8", errors="ignore")
    return data[:limit].decode("utf-8", errors="ignore")


def is_error_page(data):
    if not data:
        return False
    try:
        text = decode_text(data, 1000).lower()
        indicators = [
            "<html",
            "<!doctype",
            "404 not found",
            "403 forbidden",
            "access denied",
            "error",
            "unauthorized",
        ]
        return any(ind in text for ind in indicators)
    except Exception:
        return False


def is_playlist_content(data):
    try:
        preview = decode_text(data, 500)
        return "#EXTM3U" in preview or "<MPD" in preview
    except Exception:
        return False


def is_master_playlist(content):
    try:
        return b"#EXT-X-STREAM-INF" in content
    except Exception:
        return False


def is_media_playlist(content):
    try:
        text = decode_text(content, 2000)
        return any(tag in text for tag in MEDIA_PLAYLIST_TAGS)
    except Exception:
        return False


def is_mana_mana_candidate(url):
    normalized = url.lower()
    return "channels/mana-mana/" in normalized


# -------------------------------------------------------------------
# HTTP FETCH
# -------------------------------------------------------------------
def fetch_url(url, deadline, headers=None, max_retries=MAX_RETRIES):
    if headers is None:
        headers = HEADERS

    for attempt in range(max_retries + 1):
        remaining = time_left(deadline)
        if remaining <= 0:
            log_detail(f"TIMEOUT before fetch started")
            return None, False, url

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=min(TIMEOUT, max(1, remaining))) as resp:
                final_url = resp.geturl()
                chunks = []
                total = 0
                while True:
                    remaining = time_left(deadline)
                    if remaining <= 0:
                        log_detail(f"TIMEOUT while reading response")
                        return None, False, final_url
                    if total >= MAX_READ_BYTES:
                        break
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                data = b"".join(chunks)
                return data, True, final_url
        except Exception as e:
            log_detail(f"Fetch attempt {attempt + 1} failed: {str(e)[:80]}")
            if attempt < max_retries:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                return None, False, url
    return None, False, url


# -------------------------------------------------------------------
# PARSING
# -------------------------------------------------------------------
def parse_inner_m3u8(content, base_url):
    """
    Parse an inner .m3u8 file (like Mana-mana wrappers).
    Returns (stream_url, headers_list).
    headers_list is a list of (header_name, value) from #EXTVLCOPT lines.
    The header names are kept as EXTVLCOPT style (http-user-agent, etc.) for output,
    but we also build a real HTTP headers dict for testing (mapped).
    """
    raw_headers = []   # list of (opt_key, value) as they appear
    try:
        text = decode_text(content)
    except Exception:
        return None, []

    lines = text.splitlines()
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.startswith("#EXTVLCOPT:"):
            opt = line_stripped[len("#EXTVLCOPT:"):].strip()
            if "=" in opt:
                key, value = opt.split("=", 1)
                raw_headers.append((key, value))

    # Find stream URL
    stream_url = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("http://", "https://", "//")):
            stream_url = safe_urljoin(base_url, stripped)
            break

    return stream_url, raw_headers


def map_headers_for_request(raw_headers):
    """Convert (opt_key, value) to a dict of real HTTP headers."""
    http_headers = {}
    for key, value in raw_headers:
        real_name = EXTVLCOPT_HEADER_MAP.get(key, key)   # fallback to key itself
        http_headers[real_name] = value
    return http_headers


# -------------------------------------------------------------------
# TESTING LOGIC
# -------------------------------------------------------------------
def test_stream_playable(url, deadline, extra_headers=None, depth=0):
    """Test if a given URL is playable. Returns (working_bool, final_url, reason_str)."""
    if depth > 5:
        return False, url, "max recursion depth"

    remaining = time_left(deadline)
    if remaining <= 0:
        return False, url, "timeout"

    merged_headers = HEADERS.copy()
    if extra_headers:
        merged_headers.update(extra_headers)

    data, success, final_url = fetch_url(url, deadline, headers=merged_headers)
    if not success or not data:
        return False, url, "fetch failed or timeout"

    if is_error_page(data):
        return False, url, "error page (HTML)"

    if is_playlist_content(data):
        if is_master_playlist(data):
            # follow first variant
            variant = None
            text = decode_text(data)
            lines = text.splitlines()
            capture = False
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#EXT-X-STREAM-INF"):
                    capture = True
                    continue
                if stripped.startswith("#"):
                    continue
                if capture:
                    variant = safe_urljoin(final_url, stripped)
                    break
            if variant:
                return test_stream_playable(
                    variant, deadline, extra_headers=extra_headers, depth=depth + 1
                )
            else:
                return False, url, "master playlist without variants"
        else:
            # media playlist or simple M3U
            return True, final_url, "playable (media playlist)"
    else:
        # Maybe direct stream
        return True, final_url, "assumed direct stream"


def test_candidate(url):
    """Test a single candidate URL.
    Returns (working_bool, output_url_or_original, headers_list_for_output, reason).
    For Mana-mana: output_url is the unwrapped inner URL, headers_list_for_output
    contains the raw EXTVLCOPT headers to embed in the final playlist.
    """
    deadline = time.monotonic() + CANDIDATE_TIMEOUT
    log_detail(f"--- Testing candidate: {url[:120]}...")

    if is_mana_mana_candidate(url):
        log_detail("Mana-mana candidate - fetching inner wrapper")
        data, success, final_url = fetch_url(url, deadline)
        if not success or not data:
            return False, url, None, "failed to fetch Mana-mana wrapper"
        stream_url, raw_headers = parse_inner_m3u8(data, final_url)
        if not stream_url:
            return False, url, None, "no stream URL found in wrapper"
        log_detail(f"Unwrapped stream URL: {stream_url[:120]}...")
        # Map EXTVLCOPT headers to real HTTP headers for testing
        http_headers = map_headers_for_request(raw_headers)
        working, resolved_stream_url, reason = test_stream_playable(
            stream_url, deadline, extra_headers=http_headers
        )
        if working:
            # Return the resolved URL and the raw headers (so they can be written back)
            return True, resolved_stream_url, raw_headers, f"Mana-mana unwrapped: {reason}"
        else:
            return False, url, None, f"Mana-mana inner stream: {reason}"
    else:
        # Normal candidate (inner .m3u8 file)
        working, resolved, reason = test_stream_playable(url, deadline)
        if working:
            return True, url, None, f"shallow valid: {reason}"
        else:
            return False, url, None, f"shallow invalid: {reason}"


def process_channel(extinf_line, candidates, channel_num, total_channels):
    safe = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    log_progress(channel_num, total_channels, f"Testing: {safe}")
    log_detail(f"Channel has {len(candidates)} candidate(s)")

    if len(candidates) == 1:
        url = candidates[0]
        working, output_url, raw_headers, reason = test_candidate(url)
        if working:
            log_progress(channel_num, total_channels, f"✓ Working")
            return extinf_line, output_url, raw_headers, True, reason
        else:
            log_progress(channel_num, total_channels, f"✗ Failed ({reason})")
            return extinf_line, url, None, False, reason
    else:
        # Multiple candidates: test in parallel with shutdown after first success
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            futures = {executor.submit(test_candidate, url): url for url in candidates}
            try:
                for future in as_completed(futures):
                    working, output_url, raw_headers, reason = future.result()
                    if working:
                        # cancel remaining futures
                        executor.shutdown(wait=False, cancel_futures=True)
                        log_progress(channel_num, total_channels, f"✓ Working")
                        return extinf_line, output_url, raw_headers, True, reason
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        # All failed
        log_progress(channel_num, total_channels, f"✗ All candidates failed")
        return extinf_line, candidates[0], None, False, reason


# -------------------------------------------------------------------
# MAIN VALIDATION
# -------------------------------------------------------------------
def validate():
    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found. Run flatten.py first.")
        sys.exit(1)

    print("=" * 60)
    print("IPTV Playlist Validator & Mana-mana Unwrapper")
    print("=" * 60)

    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Count channels
    total_channels = sum(1 for line in lines if line.startswith("#EXTINF:"))
    print(f"Total channels to validate: {total_channels}")

    output = []
    report_lines = ["Channel Name,Status,Reason"]
    i = 0
    current = 0

    while i < len(lines):
        line = lines[i].rstrip("\n\r")
        if line.startswith("#EXTM3U"):
            output.append(line)
            i += 1
            continue

        if line.startswith("#EXTINF:"):
            extinf = line
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1

            candidates = []
            while i < len(lines):
                nxt = lines[i].rstrip("\n\r").strip()
                if not nxt:
                    i += 1
                    continue
                if nxt.startswith("#EXTINF:") or nxt.startswith("#EXTM3U"):
                    break
                if nxt.startswith("#"):
                    i += 1
                    continue
                if nxt.startswith(("http://", "https://")):
                    candidates.append(nxt)
                i += 1

            if not candidates:
                output.append(extinf)
                continue

            channel_name = extinf.rsplit(",", 1)[-1].strip()
            current += 1
            final_extinf, final_url, raw_headers, success, reason = process_channel(
                extinf, candidates, current, total_channels
            )

            if success:
                # Write EXTVLCOPT lines before URL (only if raw_headers provided)
                output.append(final_extinf)
                if raw_headers:
                    for k, v in raw_headers:
                        output.append(f"#EXTVLCOPT:{k}={v}")
                output.append(final_url)
                status = "PASS"
            else:
                # Comment out the whole entry
                output.append(f"##{final_extinf}")
                output.append(f"##{final_url}")
                status = "FAIL"

            report_lines.append(f"{channel_name},{status},{reason}")
        else:
            output.append(line)
            i += 1

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(output) + "\n")

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print("\n" + "=" * 60)
    print(f"Validated playlist written to {OUTPUT_FILE}")
    print(f"Report written to {REPORT_FILE}")
    print(f"   Total lines: {len(output)}")
    print("=" * 60)


if __name__ == "__main__":
    validate()
