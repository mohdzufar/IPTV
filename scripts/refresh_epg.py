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


def convert_xmltv_time(value):
    """Strip any timezone offset and return just the timestamp (YYYYMMDDHHMMSS).
    Players will treat the time as local (UTC+8) automatically."""
    value = value.strip()
    match = XMLTV_TIME_RE.match(value)
    if not match:
        return value, False

    timestamp, offset_text = match.groups()
    if len(timestamp) == 12:
        timestamp += "00"

    # Return the timestamp without any offset suffix
    return timestamp, True


def convert_epg_times(root):
    """Remove timezone offsets from all programme start/stop times."""
    converted_count = 0

    for programme in root.findall("programme"):
        for attr in ("start", "stop"):
            if attr not in programme.attrib:
                continue

            converted, changed = convert_xmltv_time(programme.attrib[attr])
            if changed:
                programme.attrib[attr] = converted
                converted_count += 1

    return converted_count


def main():
    log("=" * 60)
    log("Refreshing EPG – stripping timezone offsets for OTT Navigator")
    log("=" * 60)

    xml_text = fetch_source_epg()
    root = ET.fromstring(xml_text.encode("utf-8"))

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
