#!/usr/bin/env python3
import gzip
import io
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "EPG" / "epg.xml.gz"

SOURCE_EPG_URL = "https://epg.pw/xmltv/epg.xml.gz"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

MYT_OFFSET = "+0800"

# Regex to capture YYYYMMDDHHMMSS and optional timezone offset
XMLTV_TIME_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?$")


def log(message):
    print(message, flush=True)


def fetch_source_epg():
    log(f"Fetching source EPG: {SOURCE_EPG_URL}")

    request = urllib.request.Request(
        SOURCE_EPG_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/xml,text/xml,*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()

    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)

    return data.decode("utf-8-sig", errors="replace")


def localize_time(value):
    """
    Take an XMLTV time string (e.g., "20260601070000 +0000")
    and return the same clock time but with +0800 offset.
    The source times are already Malaysia local time, just mislabeled.
    """
    value = value.strip()
    match = XMLTV_TIME_RE.match(value)
    if not match:
        return value, False

    timestamp, _ = match.groups()  # ignore original offset
    if len(timestamp) == 12:
        timestamp += "00"

    # Return with explicit +0800 so the player knows it's local time
    return f"{timestamp} {MYT_OFFSET}", True


def convert_epg_times(root):
    """Fix programme times: keep clock time, add +0800."""
    converted_count = 0

    for programme in root.findall("programme"):
        for attr in ("start", "stop"):
            if attr not in programme.attrib:
                continue

            new_value, changed = localize_time(programme.attrib[attr])
            if changed and new_value != programme.attrib[attr]:
                programme.attrib[attr] = new_value
                converted_count += 1

    return converted_count


def main():
    log("=" * 60)
    log("Refreshing EPG – setting times to Malaysia local (+0800)")
    log("=" * 60)

    xml_text = fetch_source_epg()
    root = ET.fromstring(xml_text.encode("utf-8"))

    # Set global <tv> date to current Malaysia time
    now_myt = datetime.now(timezone(timedelta(hours=8)))
    root.set("date", now_myt.strftime("%Y%m%d%H%M%S") + " " + MYT_OFFSET)

    channel_count = len(root.findall("channel"))
    programme_count = len(root.findall("programme"))
    converted_count = convert_epg_times(root)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with gzip.open(OUTPUT_FILE, "wb", compresslevel=9) as handle:
        handle.write(xml_bytes)
        handle.write(b"\n")

    old_uncompressed = REPO_ROOT / "EPG" / "epg.xml"
    if old_uncompressed.exists():
        old_uncompressed.unlink()

    log(f"Source         : {SOURCE_EPG_URL}")
    log(f"Channels       : {channel_count}")
    log(f"Programmes     : {programme_count}")
    log(f"Timestamps fixed: {converted_count}")
    log(f"Output         : {OUTPUT_FILE}")
    log("=" * 60)


if __name__ == "__main__":
    main()
