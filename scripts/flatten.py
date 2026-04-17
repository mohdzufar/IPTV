#!/usr/bin/env python3
"""
IPTV Playlist Flattener for GitHub Actions
Reads Main.m3u8, resolves sub-playlist URLs, and writes Main_Flattened.m3u8
with direct stream URLs only.
"""

import re
import urllib.request
import urllib.error
import sys
from pathlib import Path

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
MAIN_FILE = "Main.m3u8"
OUTPUT_FILE = "Main_Flattened.m3u8"

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
def fetch_url_content(url, timeout=10):
    """Fetch text content from a URL."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"  ⚠️ Failed to fetch {url}: {e}")
        return None

def extract_stream_url_from_playlist(content):
    """
    Extract the first usable stream URL from a playlist file.
    Handles both simple redirect playlists and HLS master playlists.
    """
    if not content:
        return None
    
    lines = content.splitlines()
    
    # Look for the first non-comment line that is a URL
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # It's a URL line
        return line
    
    return None

def is_sub_playlist_url(url):
    """
    Determine if a URL points to a sub-playlist file that needs flattening.
    We consider raw.githubusercontent.com URLs and any .m3u/.m3u8 files.
    """
    url_lower = url.lower()
    return ('raw.githubusercontent.com' in url_lower and 
            (url_lower.endswith('.m3u') or url_lower.endswith('.m3u8')))

def process_main_playlist(main_path):
    """Read Main.m3u8, resolve sub-playlists, and return flattened lines."""
    with open(main_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    flattened = []
    i = 0
    
    while i < len(lines):
        line = lines[i].rstrip('\n\r')
        
        # Preserve the #EXTM3U header and any comment lines that aren't channel entries
        if line.startswith('#EXTM3U'):
            flattened.append(line)
            i += 1
            continue
        
        # Detect a channel entry: #EXTINF line followed by a URL
        if line.startswith('#EXTINF:'):
            extinf_line = line
            i += 1
            
            # Skip any blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1
            
            if i < len(lines):
                url_line = lines[i].rstrip('\n\r').strip()
                i += 1
                
                if is_sub_playlist_url(url_line):
                    print(f"  Resolving: {url_line[:60]}...")
                    content = fetch_url_content(url_line)
                    stream_url = extract_stream_url_from_playlist(content)
                    
                    if stream_url:
                        print(f"    -> {stream_url[:60]}...")
                        flattened.append(extinf_line)
                        flattened.append(stream_url)
                    else:
                        print(f"    -> Failed to extract, keeping original URL")
                        flattened.append(extinf_line)
                        flattened.append(url_line)
                else:
                    # Direct stream URL, keep as-is
                    flattened.append(extinf_line)
                    flattened.append(url_line)
            else:
                # EOF reached without URL, preserve the #EXTINF anyway
                flattened.append(extinf_line)
        else:
            # Preserve other lines (comments, blank lines, etc.)
            flattened.append(line)
            i += 1
    
    return flattened

def main():
    print("=" * 50)
    print("IPTV Playlist Flattener")
    print("=" * 50)
    
    # Check if Main.m3u8 exists
    if not Path(MAIN_FILE).exists():
        print(f"❌ Error: {MAIN_FILE} not found in current directory.")
        sys.exit(1)
    
    print(f"📂 Reading {MAIN_FILE}...")
    flattened_lines = process_main_playlist(MAIN_FILE)
    
    # Write output file
    output_path = Path(OUTPUT_FILE)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(flattened_lines))
    
    print(f"✅ Flattened playlist written to {OUTPUT_FILE}")
    print(f"   Total lines: {len(flattened_lines)}")
    print("=" * 50)

if __name__ == "__main__":
    main()
