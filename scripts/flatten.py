#!/usr/bin/env python3
"""
Simple IPTV Playlist Flattener (no validation).
Reads Channels/Flatten.m3u8 and for each channel takes the first candidate URL.
Outputs Main.m3u8 without any network tests.
"""

import sys
from pathlib import Path

SOURCE_FILE = "Channels/Flatten.m3u8"
OUTPUT_FILE = "Main.m3u8"


def flatten():
    if not Path(SOURCE_FILE).exists():
        print(f"Error: {SOURCE_FILE} not found.")
        sys.exit(1)

    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    output = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n\r")

        if line.startswith("#EXTM3U"):
            output.append(line)
            i += 1
            continue

        if line.startswith("#EXTINF:"):
            extinf = line
            i += 1
            # skip blank lines
            while i < len(lines) and not lines[i].strip():
                i += 1

            # pick the first URL candidate
            url = None
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
                    url = nxt
                    i += 1
                    break
                i += 1

            if url:
                output.append(extinf)
                output.append(url)
            else:
                # comment out if no URL found
                output.append(f"##{extinf}")
                output.append("##NO_URL")
        else:
            output.append(line)
            i += 1

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(output) + "\n")

    print(f"Flattened playlist written to {OUTPUT_FILE} ({len(output)} lines).")


if __name__ == "__main__":
    flatten()
