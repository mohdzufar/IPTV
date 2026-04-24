#!/usr/bin/env python3
"""
IPTV Playlist Validator and TV-safe post-processor.

Flow:
- Reads Main.m3u8 produced by scripts/flatten.py
- Mana-mana:
  - fetch wrapper file
  - parse inner stream URL + EXTVLCOPT headers
  - validate the inner stream using mapped HTTP headers
  - write EXTVLCOPT + direct session URL back into Main.m3u8
- Njoi:
  - fetch wrapper file
  - inspect inner stream URL
  - if inner stream is DASH (.mpd), mark FAIL/comment out because many TV apps do not support it
  - if inner stream is HLS/direct, keep the original wrapper URL in Main.m3u8
- Other channels:
  - shallow validation only
  - keep original URL in Main.m3u8 when valid
- Writes validation-report.txt
"""

import sys
import time
import io
import gzip
import zlib
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Main.m3u8"
OUTPUT_FILE = "Main.m3u8"
REPORT_FILE = "validation-report.txt"

TIMEOUT = 15
CANDIDATE_TIMEOUT = 15
MAX_RETRIES = 1
RETRY_DELAY = 2
MAX_READ_BYTES = 2 * 1024 * 1024  # 2 MB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
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

EXTVLCOPT_HEADER_MAP = {
    "http-user-agent": "User-Agent",
    "http-referrer": "Referer",
}

# -------------------------------------------------------------------
# LOGGING
# -------------------------------------------------------------------
def log_progress(channel_num, total_channels, message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] Ch {channel_num}/{total_channels}: {message}")


def log_detail(message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"      [{timestamp}] {message}")


# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
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


def is_mana_mana_candidate(url):
    return "channels/mana-mana/" in url.lower()


def is_njoi_candidate(url):
    return "channels/njoi/" in url.lower()


def decompress_response(data, content_encoding):
    if not data:
        return data

    encoding = (content_encoding or "").lower().strip()
    if not encoding:
        return data

    try:
        if "gzip" in encoding:
            return gzip.decompress(data)
        if "deflate" in encoding:
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
    except Exception as e:
        log_detail(f"Decompression failed: {str(e)[:80]}")

    return data


def map_headers_for_request(raw_headers):
    http_headers = {}
    for key, value in raw_headers:
        real_name = EXTVLCOPT_HEADER_MAP.get(key, key)
        http_headers[real_name] = value
    return http_headers


# -------------------------------------------------------------------
# FETCH
# -------------------------------------------------------------------
def fetch_url(url, deadline, headers=None, max_retries=MAX_RETRIES):
    request_headers = HEADERS.copy()
    if headers:
        request_headers.update(headers)

    for attempt in range(max_retries + 1):
        remaining = time_left(deadline)
        if remaining <= 0:
            log_detail("TIMEOUT before fetch started")
            return None, False, url

        try:
            req = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(req, timeout=min(TIMEOUT, max(1, remaining))) as resp:
                final_url = resp.geturl()
                content_encoding = resp.headers.get("Content-Encoding", "")

                chunks = []
                total = 0

                while True:
                    remaining = time_left(deadline)
                    if remaining <= 0:
                        log_detail("TIMEOUT while reading response")
                        return None, False, final_url

                    if total >= MAX_READ_BYTES:
                        break

                    chunk = resp.read(min(8192, MAX_READ_BYTES - total))
                    if not chunk:
                        break

                    chunks.append(chunk)
                    total += len(chunk)

                raw_data = b"".join(chunks)
                data = decompress_response(raw_data, content_encoding)
                return data, True, final_url

        except Exception as e:
            log_detail(f"Fetch attempt {attempt + 1} failed: {str(e)[:80]}")
            if attempt < max_retries and time_left(deadline) > RETRY_DELAY:
                time.sleep(RETRY_DELAY)
            else:
                return None, False, url

    return None, False, url


# -------------------------------------------------------------------
# PARSING
# -------------------------------------------------------------------
def extract_first_variant_url(content, base_url):
    try:
        text = decode_text(content)
    except Exception:
        return None

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
            return safe_urljoin(base_url, stripped)

    return None


def parse_wrapper_m3u(content, base_url):
    raw_headers = []
    urls = []

    try:
        text = decode_text(content)
    except Exception:
        return urls, raw_headers

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#EXTVLCOPT:"):
            opt = stripped[len("#EXTVLCOPT:"):].strip()
            if "=" in opt:
                key, value = opt.split("=", 1)
                raw_headers.append((key.strip(), value.strip()))
            continue

        if stripped.startswith("#"):
            continue

        if stripped.startswith(("http://", "https://", "//")):
            urls.append(safe_urljoin(base_url, stripped))

    return urls, raw_headers


def classify_content(data, url_hint=""):
    lower_url = url_hint.lower()
    text = decode_text(data, 3000)

    if "<MPD" in text or lower_url.endswith(".mpd"):
        return "dash"

    if "#EXTM3U" in text:
        if "#EXT-X-STREAM-INF" in text:
            return "hls-master"
        if any(tag in text for tag in MEDIA_PLAYLIST_TAGS):
            return "hls-media"

        urls, _ = parse_wrapper_m3u(data, url_hint)
        if urls:
            return "wrapper"

        return "m3u"

    return "other"


# -------------------------------------------------------------------
# VALIDATION
# -------------------------------------------------------------------
def validate_hls_or_direct(url, deadline, extra_headers=None, depth=0):
    if depth > 5:
        return False, "max recursion depth"

    if time_left(deadline) <= 0:
        return False, "timeout"

    data, success, final_url = fetch_url(url, deadline, headers=extra_headers)
    if not success or not data:
        return False, "fetch failed or timeout"

    if is_error_page(data):
        return False, "error page (HTML)"

    kind = classify_content(data, final_url)

    if kind == "dash":
        return False, "DASH (.mpd) not TV-safe"

    if kind == "hls-master":
        variant_url = extract_first_variant_url(data, final_url)
        if not variant_url:
            return False, "HLS master without variants"
        ok, reason = validate_hls_or_direct(
            variant_url,
            deadline,
            extra_headers=extra_headers,
            depth=depth + 1,
        )
        if ok:
            return True, f"HLS master -> {reason}"
        return False, reason

    if kind == "hls-media":
        return True, "HLS media playlist"

    if kind == "wrapper":
        return True, "simple wrapper playlist"

    if kind == "m3u":
        return True, "M3U playlist"

    return True, "assumed direct stream"


def validate_mana_candidate(wrapper_url, deadline):
    log_detail("Mana-mana candidate - fetching wrapper")

    data, success, final_url = fetch_url(wrapper_url, deadline)
    if not success or not data:
        return False, wrapper_url, None, "failed to fetch Mana-mana wrapper"

    inner_urls, raw_headers = parse_wrapper_m3u(data, final_url)
    if not inner_urls:
        return False, wrapper_url, None, "no stream URL found in wrapper"

    inner_url = inner_urls[0]
    http_headers = map_headers_for_request(raw_headers)

    log_detail(f"Mana inner URL: {inner_url[:120]}...")

    ok, reason = validate_hls_or_direct(inner_url, deadline, extra_headers=http_headers)
    if not ok:
        return False, wrapper_url, None, f"Mana-mana inner stream: {reason}"

    # Important: output the original fresh inner session URL, not the deepest variant URL.
    return True, inner_url, raw_headers, f"Mana-mana unwrapped: {reason}"


def validate_njoi_candidate(wrapper_url, deadline):
    log_detail("Njoi candidate - fetching wrapper")

    data, success, final_url = fetch_url(wrapper_url, deadline)
    if not success or not data:
        return False, wrapper_url, None, "failed to fetch Njoi wrapper"

    inner_urls, _ = parse_wrapper_m3u(data, final_url)
    if not inner_urls:
        return False, wrapper_url, None, "no stream URL found in wrapper"

    inner_url = inner_urls[0]
    log_detail(f"Njoi inner URL: {inner_url[:120]}...")

    if inner_url.lower().endswith(".mpd"):
        return False, wrapper_url, None, "Njoi inner stream is DASH (.mpd), not TV-safe"

    ok, reason = validate_hls_or_direct(inner_url, deadline)
    if not ok:
        return False, wrapper_url, None, f"Njoi inner stream: {reason}"

    # Keep wrapper URL in output for Njoi if it is TV-safe.
    return True, wrapper_url, None, f"Njoi wrapper valid: {reason}"


def validate_generic_candidate(url, deadline):
    ok, reason = validate_hls_or_direct(url, deadline)
    if not ok:
        return False, url, None, f"generic: {reason}"

    # Keep original URL in output for non-Mana channels.
    return True, url, None, f"generic: {reason}"


def test_candidate(url):
    deadline = time.monotonic() + CANDIDATE_TIMEOUT
    log_detail(f"--- Testing candidate: {url[:120]}...")

    if is_mana_mana_candidate(url):
        return validate_mana_candidate(url, deadline)

    if is_njoi_candidate(url):
        return validate_njoi_candidate(url, deadline)

    return validate_generic_candidate(url, deadline)


def process_channel(extinf_line, candidates, channel_num, total_channels):
    safe = extinf_line[:60] + "..." if len(extinf_line) > 60 else extinf_line
    log_progress(channel_num, total_channels, f"Testing: {safe}")
    log_detail(f"Channel has {len(candidates)} candidate(s)")

    last_reason = "all candidates failed"

    for idx, url in enumerate(candidates, 1):
        log_detail(f"Testing candidate {idx}/{len(candidates)}")
        success, output_url, raw_headers, reason = test_candidate(url)
        last_reason = reason

        if success:
            log_progress(channel_num, total_channels, "PASS")
            return extinf_line, output_url, raw_headers, True, reason

    log_progress(channel_num, total_channels, f"FAIL ({last_reason})")
    return extinf_line, candidates[0], None, False, last_reason


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
def validate():
    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found. Run flatten.py first.")
        sys.exit(1)

    print("=" * 60)
    print("IPTV Playlist Validator & TV-safe Post-Processor")
    print("=" * 60)

    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

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
                extinf,
                candidates,
                current,
                total_channels,
            )

            if success:
                output.append(final_extinf)
                if raw_headers:
                    for key, value in raw_headers:
                        output.append(f"#EXTVLCOPT:{key}={value}")
                output.append(final_url)
                status = "PASS"
            else:
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
    print(f"Total output lines: {len(output)}")
    print("=" * 60)


if __name__ == "__main__":
    validate()
