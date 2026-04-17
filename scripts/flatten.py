#!/usr/bin/env python3
"""
IPTV Playlist Flattener with Reliable URL Health Check
- Tests multiple candidate URLs using Range request (bytes=0-0).
- Selects first working URL, flattening sub-playlists if needed.
- Preserves master playlists for quality options.
- Outputs clean single-URL-per-channel Main.m3u8.
"""

import urllib.request
import urllib.error
import sys
from pathlib import Path

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
SOURCE_FILE = "Channels/Flatten.m3u8"   # The file you edit
OUTPUT_FILE = "Main.m3u8"               # Flattened output for users

CHECK_TIMEOUT = 8   # Seconds to wait for server response

# -------------------------------------------------------------------
# URL TESTING (Range Request Method)
# -------------------------------------------------------------------
def is_url_reachable(url, timeout=CHECK_TIMEOUT):
    """
    Test if a stream/server is alive using a Range request (first byte).
    More reliable than HEAD because many IPTV servers block HEAD.
    """
    try:
        req = urllib.request.Request(url, method='GET', headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Range': 'bytes=0-0'   # Request only the first byte
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # 200 OK or 206 Partial Content are acceptable
            return resp.status in (200, 206)
    except Exception:
        return False

def fetch_url_content(url, timeout=CHECK_TIMEOUT):
    """Fetch full content of a playlist file."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_bytes = response.read()
            try:
                return content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                return content_bytes.decode('latin-1')
    except Exception as e:
        print(f"      ⚠️ Fetch failed: {e}")
        return None

def is_master_playlist(content):
    """Return True if playlist contains #EXT-X-STREAM-INF (master playlist)."""
    return content and '#EXT-X-STREAM-INF' in content

def extract_stream_url_from_simple_playlist(content):
    """Extract first non‑comment line as stream URL from a simple playlist."""
    if not content:
        return None
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            return line
    return None

def should_treat_as_playlist(url):
    """Heuristic: does URL likely point to a playlist file?"""
    url_lower = url.lower()
    return any(ext in url_lower for ext in ['.m3u', '.m3u8'])

def test_and_resolve_url(url):
    """
    Test a single candidate URL. If it's a playlist, flatten it.
    Returns (final_url, is_working).
    """
    # Step 1: Check basic reachability
    if not is_url_reachable(url):
        print(f"      ❌ Unreachable: {url[:60]}...")
        return url, False

    # Step 2: If it's a direct stream (not a playlist), we're done
    if not should_treat_as_playlist(url):
        print(f"      ✅ Direct stream reachable: {url[:60]}...")
        return url, True

    # Step 3: It's a playlist – fetch and inspect
    print(f"      📄 Checking playlist: {url[:60]}...")
    content = fetch_url_content(url)
    if not content:
        return url, False

    # Step 4: If master playlist, keep original (player handles quality selection)
    if is_master_playlist(content):
        print(f"      ✅ Master playlist (keeping original)")
        return url, True

    # Step 5: Simple playlist – extract direct stream URL
    stream_url = extract_stream_url_from_simple_playlist(content)
    if stream_url:
        # Test the extracted stream URL for extra safety
        if is_url_reachable(stream_url):
            print(f"      ➡️ Flattened to working stream: {stream_url[:60]}...")
            return stream_url, True
        else:
            print(f"      ⚠️ Extracted stream unreachable, keeping playlist")
            return url, False
    else:
        print(f"      ⚠️ No stream URL found in playlist")
        return url, False

def process_channel(extinf_line, candidate_urls):
    """
    Given an #EXTINF line and a list of candidate URLs,
    test each in order and return the first working one.
    Returns (extinf_line, final_url) or (extinf_line, fallback_url).
    """
    print(f"  Testing candidates for: {extinf_line[:50]}...")
    for idx, url in enumerate(candidate_urls, 1):
        print(f"    Candidate {idx}: {url[:70]}...")
        final_url, working = test_and_resolve_url(url)
        if working:
            print(f"    ✅ Selected candidate {idx}")
            return extinf_line, final_url

    # All failed – fallback to first URL (so channel entry remains)
    print(f"    ❌ All candidates failed, using first as fallback")
    return extinf_line, candidate_urls[0]

def process_source_playlist(source_path):
    """Read source file, test URLs, and output single working URL per channel."""
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    flattened = []
    i = 0
    channel_count = 0

    while i < len(lines):
        line = lines[i].rstrip('\n\r')

        # Preserve #EXTM3U header
        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue

        # Channel entry: #EXTINF line
        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1

            # Skip blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1

            # Collect all consecutive candidate URLs
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
            print(f"\n📺 Channel {channel_count}:")
            final_extinf, final_url = process_channel(extinf_line, candidates)

            flattened.append(final_extinf)
            flattened.append(final_url)
        else:
            # Preserve comments and other non‑channel lines
            flattened.append(line)
            i += 1

    return flattened

def main():
    print("=" * 60)
    print("IPTV Playlist Flattener (Range Request Health Check)")
    print("=" * 60)

    if not Path(SOURCE_FILE).exists():
        print(f"❌ Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    print(f"📂 Reading {SOURCE_FILE}...")
    flattened_lines = process_source_playlist(SOURCE_FILE)

    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))

    print("\n" + "=" * 60)
    print(f"✅ Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
