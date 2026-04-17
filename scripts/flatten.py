#!/usr/bin/env python3
"""
IPTV Playlist Flattener (Smart Version)
- Detects master playlists (HLS multi-bitrate) and preserves their URLs.
- Only flattens simple redirect playlists (single stream).
- Handles various hosting services, not just GitHub raw.
"""

import re
import urllib.request
import urllib.error
import sys
from pathlib import Path

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
MAIN_FILE = "Flattened.m3u8"
OUTPUT_FILE = "Main.m3u8"

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_url_content(url, timeout=15):
    """Fetch text content from a URL with a browser-like User-Agent."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # Read as bytes and decode safely
            content_bytes = response.read()
            # Try UTF-8, fallback to Latin-1
            try:
                return content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                return content_bytes.decode('latin-1')
    except Exception as e:
        print(f"  ⚠️ Failed to fetch {url}: {e}")
        return None

def is_master_playlist(content):
    """Return True if the playlist content contains #EXT-X-STREAM-INF."""
    if not content:
        return False
    return '#EXT-X-STREAM-INF' in content

def extract_stream_url_from_simple_playlist(content):
    """
    Extract the first usable stream URL from a SIMPLE playlist.
    (Only called for non-master playlists.)
    """
    if not content:
        return None
    
    lines = content.splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Found a URL line
        return line
    return None

def should_treat_as_playlist(url):
    """
    Heuristic to decide if a URL likely points to a playlist file.
    Returns True if the URL ends with common playlist extensions.
    """
    url_lower = url.lower()
    playlist_extensions = ('.m3u', '.m3u8', '.m3u?', '.m3u8?')
    return any(url_lower.endswith(ext) or ext in url_lower for ext in playlist_extensions)

def process_main_playlist(main_path):
    """Read Main.m3u8, resolve sub-playlists intelligently, and return flattened lines."""
    with open(main_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    flattened = []
    i = 0
    processed_count = 0
    master_count = 0
    flattened_count = 0
    skipped_count = 0
    
    while i < len(lines):
        line = lines[i].rstrip('\n\r')
        
        # Always preserve #EXTM3U header and global comments
        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue
        
        # Detect channel entry: #EXTINF line
        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1
            
            # Skip blank lines between #EXTINF and URL
            while i < len(lines) and not lines[i].strip():
                i += 1
            
            if i < len(lines):
                url_line = lines[i].rstrip('\n\r').strip()
                i += 1
                
                # Only process if it looks like a playlist file
                if should_treat_as_playlist(url_line):
                    processed_count += 1
                    print(f"  Checking: {url_line[:70]}...")
                    
                    content = fetch_url_content(url_line)
                    
                    if content:
                        if is_master_playlist(content):
                            # It's a master playlist - keep the original URL so quality options remain
                            master_count += 1
                            print(f"    ✅ Master playlist detected, keeping original URL")
                            flattened.append(extinf_line)
                            flattened.append(url_line)
                        else:
                            # Simple playlist - extract the direct stream URL
                            stream_url = extract_stream_url_from_simple_playlist(content)
                            if stream_url:
                                flattened_count += 1
                                print(f"    ➡️ Flattened to: {stream_url[:60]}...")
                                flattened.append(extinf_line)
                                flattened.append(stream_url)
                            else:
                                skipped_count += 1
                                print(f"    ⚠️ Could not extract stream URL, keeping original")
                                flattened.append(extinf_line)
                                flattened.append(url_line)
                    else:
                        skipped_count += 1
                        print(f"    ❌ Failed to fetch, keeping original URL")
                        flattened.append(extinf_line)
                        flattened.append(url_line)
                else:
                    # Not a playlist URL (direct stream) - keep as-is
                    flattened.append(extinf_line)
                    flattened.append(url_line)
            else:
                # EOF without URL
                flattened.append(extinf_line)
        else:
            # Preserve other lines (comments, blank lines, etc.)
            flattened.append(line)
            i += 1
    
    print("\n" + "=" * 50)
    print("SUMMARY:")
    print(f"  Total sub-playlists processed: {processed_count}")
    print(f"  Master playlists (preserved):  {master_count}")
    print(f"  Simple playlists (flattened):   {flattened_count}")
    print(f"  Skipped/Failed:                 {skipped_count}")
    print("=" * 50)
    
    return flattened

def main():
    print("=" * 50)
    print("IPTV Playlist Flattener (Smart)")
    print("=" * 50)
    
    if not Path(MAIN_FILE).exists():
        print(f"❌ Error: {MAIN_FILE} not found in current directory.")
        sys.exit(1)
    
    print(f"📂 Reading {MAIN_FILE}...")
    flattened_lines = process_main_playlist(MAIN_FILE)
    
    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))
    
    print(f"\n✅ Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 50)

if __name__ == "__main__":
    main()
