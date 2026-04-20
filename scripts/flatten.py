#!/usr/bin/env python3
"""
IPTV Playlist Flattener – Health Validator Mode
- Tests all candidates in Flatten.m3u8 sequentially.
- For sub‑playlists, tests all internal URLs in order until one works.
- Keeps the original candidate URL (sub‑playlist) if any internal stream is playable.
- Outputs the first working candidate URL; if none, comments out the first candidate.
- Handles Master HLS, DASH (with XML declaration), and direct streams.
"""

import urllib.request
import urllib.error
import sys
import time
import re
import io
from pathlib import Path
from urllib.parse import urljoin

# Force UTF-8 output to avoid UnicodeEncodeError on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"
OUTPUT_FILE = "Main.m3u8"

CHUNK_SIZE = 262144        # 256 KB
TIMEOUT = 15
MAX_RETRIES = 2
RETRY_DELAY = 2

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'identity',
    'Connection': 'keep-alive'
}

VALID_HLS_MIME = {'application/vnd.apple.mpegurl', 'audio/mpegurl', 'application/x-mpegURL'}
VALID_DASH_MIME = {'application/dash+xml'}

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_with_retry(url, timeout=TIMEOUT, max_retries=MAX_RETRIES, chunk_size=None):
    """Fetch URL content with retries. Returns (data, success, content_type)."""
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get('Content-Type', '').lower()
                if chunk_size:
                    data = resp.read(chunk_size)
                else:
                    data = resp.read()
                return data, True, content_type
        except Exception:
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                return None, False, ''

def is_valid_media_data(data):
    """Check if downloaded data looks like actual video/audio."""
    if not data or len(data) < 100:
        return False
    # MPEG-TS sync byte
    if data[0] == 0x47:
        return True
    # MP4 'ftyp' box
    if len(data) > 8 and data[4:8] == b'ftyp':
        return True
    # Reject obvious HTML error pages
    try:
        text = data[:200].decode('utf-8', errors='ignore').lower()
        if any(err in text for err in ['<!doctype', '<html', '404 not found', 'error', 'unauthorized']):
            return False
    except:
        pass
    return len(data) >= 1024

def is_hls_master_playlist(content):
    """Return True if content contains #EXT-X-STREAM-INF."""
    try:
        return '#EXT-X-STREAM-INF' in content.decode('utf-8', errors='ignore')
    except:
        return False

def is_dash_manifest(content):
    """
    Return True if content is a DASH manifest (starts with <MPD,
    optionally after an XML declaration).
    """
    try:
        text = content.decode('utf-8', errors='ignore').lstrip()
        if text.startswith('<?xml'):
            end_idx = text.find('?>')
            if end_idx != -1:
                text = text[end_idx + 2:].lstrip()
        return text.startswith('<MPD')
    except:
        return False

def is_playlist_by_content(url, data):
    """Determine if the URL points to a playlist file."""
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in ('.m3u', '.m3u8', '.mpd')):
        return True
    try:
        preview = data[:200].decode('utf-8', errors='ignore')
        return '#EXT' in preview or '<MPD' in preview
    except:
        return False

def extract_first_variant_url(content, base_url):
    """Extract the first variant URI from a master playlist or DASH manifest."""
    try:
        text = content.decode('utf-8', errors='ignore')
    except:
        return None

    if '#EXT-X-STREAM-INF' in text:
        lines = text.splitlines()
        capture_next = False
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                if line.startswith('#EXT-X-STREAM-INF'):
                    capture_next = True
                continue
            if capture_next:
                return urljoin(base_url, line)
        return None

    if '<MPD' in text:
        clean_text = text.lstrip()
        if clean_text.startswith('<?xml'):
            end_idx = clean_text.find('?>')
            if end_idx != -1:
                clean_text = clean_text[end_idx + 2:].lstrip()

        base_match = re.search(r'<BaseURL>(.*?)</BaseURL>', clean_text)
        dash_base = base_match.group(1) if base_match else base_url

        init_match = re.search(r'initialization="([^"]+)"', clean_text)
        if init_match:
            return urljoin(dash_base, init_match.group(1))

        media_match = re.search(r'media="([^"]+)"', clean_text)
        if media_match:
            template = media_match.group(1)
            test_url = template.replace('$Number%09d$', '000000001').replace('$Number$', '1')
            return urljoin(dash_base, test_url)

        return None

    return None

def test_stream_playability(url):
    """
    Core test for any stream URL (direct, playlist, master, DASH).
    Returns (working: bool).
    This function is called recursively for variant/internal URLs.
    """
    data, success, content_type = fetch_with_retry(url, chunk_size=CHUNK_SIZE)
    if not success or not data:
        return False

    is_playlist = is_playlist_by_content(url, data)

    if is_playlist:
        full_data, _, full_content_type = fetch_with_retry(url, chunk_size=None)
        if not full_data:
            return False

        master = is_hls_master_playlist(full_data)
        dash = is_dash_manifest(full_data)

        if master or dash:
            # Validate MIME type
            if master and full_content_type not in VALID_HLS_MIME:
                return False
            if dash and full_content_type not in VALID_DASH_MIME:
                return False

            variant_url = extract_first_variant_url(full_data, url)
            if variant_url:
                return test_stream_playability(variant_url)
            else:
                return False

        # Simple redirect playlist – extract all internal URLs
        lines = full_data.decode('utf-8', errors='ignore').splitlines()
        internal_urls = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('<?xml') or line.startswith('<MPD') or line.startswith('<Period'):
                continue
            internal_urls.append(line)

        # Test each internal URL in order
        for internal_url in internal_urls:
            if test_stream_playability(internal_url):
                return True
        return False

    # Direct stream
    return is_valid_media_data(data)

def test_candidate(url):
    """
    Test a single candidate URL (from Flatten.m3u8).
    Returns (working: bool, final_url: str).
    The final_url is always the original candidate URL (not an internal stream).
    """
    print(f"      Testing candidate: {url[:70]}...")
    working = test_stream_playability(url)
    if working:
        print(f"      ✅ Candidate is playable")
    else:
        print(f"      ❌ Candidate failed")
    return working, url

def process_channel(extinf_line, candidate_urls):
    """
    Test candidates for a channel in order.
    Returns the #EXTINF line and the URL to output (or commented URL).
    """
    safe_line = extinf_line[:50] + "..." if len(extinf_line) > 50 else extinf_line
    print(f"  Testing candidates for: {safe_line}")

    for idx, url in enumerate(candidate_urls, 1):
        print(f"    Candidate {idx}: {url[:70]}...")
        working, final_url = test_candidate(url)
        if working:
            print(f"    ✅ Selected candidate {idx}")
            return extinf_line, final_url

    # All candidates failed – comment out the first candidate URL
    print(f"    ❌ All candidates failed; commenting out first candidate")
    commented_url = f"##{candidate_urls[0]}"
    return extinf_line, commented_url

def process_source_playlist(source_path):
    """Read the source file, test candidates, and build flattened output."""
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    flattened = []
    i = 0
    channel_count = 0

    while i < len(lines):
        line = lines[i].rstrip('\n\r')

        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue

        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1

            # Skip blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1

            # Collect candidate URLs (until next #EXTINF or #EXTM3U)
            candidates = []
            while i < len(lines):
                next_line = lines[i].rstrip('\n\r').strip()
                if not next_line:
                    i += 1
                    continue
                if next_line.startswith('#EXTINF:') or next_line.startswith('#EXTM3U'):
                    break
                candidates.append(next_line)
                i += 1

            if not candidates:
                flattened.append(extinf_line)
                continue

            channel_count += 1
            print(f"\nChannel {channel_count}:")
            final_extinf, final_url = process_channel(extinf_line, candidates)

            flattened.append(final_extinf)
            flattened.append(final_url)
        else:
            # Preserve comments and other lines
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener – Health Validator Mode")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"Reading {SOURCE_FILE}...")
    flattened_lines = process_source_playlist(SOURCE_FILE)

    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))

    print("\n" + "=" * 60)
    print(f"Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
