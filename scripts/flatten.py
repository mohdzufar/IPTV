#!/usr/bin/env python3
"""
IPTV Playlist Flattener - Player-Like Validation
- Tests candidates by fetching playlists and checking for error pages.
- HLS master playlists are followed to the first variant, which is then tested.
- Simple nested M3U wrapper files are unwrapped so Main.m3u8 gets the inner URL.
- Each candidate has a hard total timeout (default 15 seconds).
- If a candidate times out or fails, the script moves to the next candidate.
- Comments out both #EXTINF and URL if all candidates fail.
"""

import urllib.request
import urllib.error
import urllib.parse
import sys
import time
import io
from pathlib import Path
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"
OUTPUT_FILE = "Main.m3u8"

# Per socket/request timeout
TIMEOUT = 15

# Hard wall-clock timeout for one candidate, including nested tests
CANDIDATE_TIMEOUT = 15

MAX_RETRIES = 1
RETRY_DELAY = 2
MAX_RECURSION_DEPTH = 5
PARALLEL_WORKERS = 4
VERBOSE = True

# Read only enough bytes to identify playlist content safely.
MAX_READ_BYTES = 262144  # 256 KB
READ_CHUNK_SIZE = 8192

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

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def log_progress(channel_num, total_channels, message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] Ch {channel_num}/{total_channels}: {message}")


def log_detail(message):
    if VERBOSE:
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


def fetch_url(url, deadline, max_retries=MAX_RETRIES, method="GET", head_only=False):
    headers = HEADERS.copy()

    for attempt in range(max_retries + 1):
        remaining = time_left(deadline)
        if remaining <= 0:
            log_detail("Candidate timeout reached before fetch started")
            return None, False, url

        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=min(TIMEOUT, max(1, remaining))) as resp:
                final_url = resp.geturl()

                if head_only:
                    return None, True, final_url

                chunks = []
                total = 0

                while True:
                    remaining = time_left(deadline)
                    if remaining <= 0:
                        log_detail("Candidate timeout reached while reading response")
                        return None, False, final_url

                    to_read = min(READ_CHUNK_SIZE, MAX_READ_BYTES - total)
                    if to_read <= 0:
                        break

                    chunk = resp.read(to_read)
                    if not chunk:
                        break

                    chunks.append(chunk)
                    total += len(chunk)

                    if total >= MAX_READ_BYTES:
                        break

                data = b"".join(chunks)
                return data, True, final_url

        except Exception as e:
            log_detail(f"Fetch attempt {attempt + 1} failed: {str(e)[:80]}")
            if attempt < max_retries:
                remaining = time_left(deadline)
                if remaining > RETRY_DELAY:
                    time.sleep(RETRY_DELAY)
            else:
                return None, False, url

    return None, False, url


def extract_first_variant_url(content, base_url):
    try:
        text = decode_text(content)
    except Exception:
        return None

    if "#EXT-X-STREAM-INF" in text:
        lines = text.splitlines()
        capture = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-STREAM-INF"):
                capture = True
                continue
            if line.startswith("#"):
                continue
            if capture:
                variant = safe_urljoin(base_url, line)
                log_detail(f"Extracted HLS variant: {variant[:100]}...")
                return variant

    return None


def extract_wrapper_urls(content, base_url):
    try:
        text = decode_text(content)
    except Exception:
        return []

    # Do not unwrap real HLS master/media playlists or DASH manifests.
    if "#EXT-X-STREAM-INF" in text:
        return []
    if any(tag in text for tag in MEDIA_PLAYLIST_TAGS):
        return []
    if "<MPD" in text:
        return []

    urls = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("http://", "https://", "//")):
            urls.append(safe_urljoin(base_url, line))

    return urls


def test_stream_playable(url, deadline, depth=0):
    if depth > MAX_RECURSION_DEPTH:
        log_detail("Max recursion depth reached")
        return False, url

    if time_left(deadline) <= 0:
        log_detail("Candidate timeout reached before stream test")
        return False, url

    log_detail(f"Testing URL: {url[:120]}...")
    data, success, final_url = fetch_url(url, deadline=deadline)
    if not success or not data:
        log_detail("Failed to fetch URL")
        return False, url

    if is_error_page(data):
        log_detail("Response appears to be an error page (HTML)")
        return False, url

    if is_playlist_content(data):
        log_detail("Detected playlist content")

        if is_master_playlist(data):
            log_detail("Master playlist detected")
            variant_url = extract_first_variant_url(data, final_url)
            if variant_url:
                log_detail("Testing variant stream...")
                working, resolved_url = test_stream_playable(variant_url, deadline, depth + 1)
                log_detail(f"Variant test result: {'PASS' if working else 'FAIL'}")
                return working, resolved_url
            log_detail("No variant URL found in master playlist")
            return False, url

        wrapper_urls = extract_wrapper_urls(data, final_url)
        if wrapper_urls:
            log_detail(f"Wrapper playlist detected with {len(wrapper_urls)} nested URL(s)")
            for idx, nested_url in enumerate(wrapper_urls, 1):
                if time_left(deadline) <= 0:
                    log_detail("Candidate timeout reached before next nested URL")
                    return False, url
                log_detail(f"Testing nested URL {idx}/{len(wrapper_urls)}")
                working, resolved_url = test_stream_playable(nested_url, deadline, depth + 1)
                if working:
                    return True, resolved_url
            log_detail("All nested URLs failed")
            return False, url

        log_detail("Media playlist detected - accepting as playable")
        return True, final_url

    log_detail("Direct stream detected - accepting as playable")
    return True, final_url


def test_candidate(url):
    deadline = time.monotonic() + CANDIDATE_TIMEOUT
    log_detail(f"--- Testing candidate: {url[:100]}...")
    working, resolved_url = test_stream_playable(url, deadline)
    if not working and time_left(deadline) <= 0:
        log_detail(f"Candidate timed out after {CANDIDATE_TIMEOUT} seconds")
    log_detail(f"Candidate result: {'PASS' if working else 'FAIL'}")
    return working, resolved_url if working else url


def process_channel(extinf_line, candidates, channel_num, total_channels):
    safe = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    log_progress(channel_num, total_channels, f"Testing: {safe}")
    log_detail(f"Channel has {len(candidates)} candidate(s)")

    if PARALLEL_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            futures = {executor.submit(test_candidate, url): url for url in candidates}
            for future in as_completed(futures):
                working, final_url = future.result()
                if working:
                    for f in futures:
                        f.cancel()
                    log_progress(channel_num, total_channels, f"✓ Working: {final_url[:80]}...")
                    return extinf_line, final_url
    else:
        for idx, url in enumerate(candidates, 1):
            log_detail(f"Testing candidate {idx}/{len(candidates)}")
            working, final_url = test_candidate(url)
            if working:
                log_progress(channel_num, total_channels, f"✓ Candidate {idx} works")
                return extinf_line, final_url

    log_progress(channel_num, total_channels, "✗ All failed; commenting out entire entry")
    commented_extinf = f"##{extinf_line}"
    commented_url = f"##{candidates[0]}"
    return commented_extinf, commented_url


def process_source_playlist(source_path):
    with open(source_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    channel_count = sum(1 for line in lines if line.startswith("#EXTINF:"))

    flattened = []
    i = 0
    current = 0

    while i < len(lines):
        line = lines[i].rstrip("\n\r")

        if line.startswith("#EXTM3U"):
            flattened.append(line)
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

            if candidates:
                current += 1
                final_extinf, final_url = process_channel(extinf, candidates, current, channel_count)
                flattened.append(final_extinf)
                flattened.append(final_url)
            else:
                flattened.append(extinf)
        else:
            flattened.append(line)
            i += 1

    return flattened


def main():
    print("=" * 60)
    print("IPTV Playlist Flattener - Player-Like Validation")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"Reading {SOURCE_FILE}...")
    start = time.time()
    result = process_source_playlist(SOURCE_FILE)
    elapsed = time.time() - start

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(result))

    print("\n" + "=" * 60)
    print(f"Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(result)}")
    print(f"   Time taken: {elapsed:.1f} seconds")
    print("=" * 60)


if __name__ == "__main__":
    main()
