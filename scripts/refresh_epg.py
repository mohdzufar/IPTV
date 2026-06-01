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

# Malaysia timezone
MYT = timezone(timedelta(hours=8))
MYT_OFFSET = "+0800"

# Regex for XMLTV timestamps (optional offset)
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


def parse_offset(offset_text):
    """Return a timezone object from an offset string like '+0000' or 'Z'."""
    if not offset_text or offset_text == "Z":
        return timezone.utc
    sign = 1 if offset_text[0] == "+" else -1
    hours = int(offset_text[1:3])
    minutes = int(offset_text[3:5])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def shift_to_myt(value):
    """
    Take an XMLTV time string (with or without offset),
    interpret it as-is, then convert to MYT (+0800).
    Returns the new time string with "+0800" suffix.
    """
    value = value.strip()
    match = XMLTV_TIME_RE.match(value)
    if not match:
        return value  # unchanged if unrecognised

    timestamp, offset_text = match.groups()
    if len(timestamp) == 12:
        timestamp += "00"

    # Determine the original timezone
    src_tz = parse_offset(offset_text) if offset_text else timezone.utc

    # Parse the naive timestamp and attach source timezone
    src_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=src_tz)

    # Convert to MYT
    myt_time = src_time.astimezone(MYT)

    return myt_time.strftime("%Y%m%d%H%M%S") + " " + MYT_OFFSET


def convert_epg_times(root):
    """Shift all programme times to MYT (+0800)."""
    converted_count = 0

    for programme in root.findall("programme"):
        for attr in ("start", "stop"):
            if attr not in programme.attrib:
                continue

            old_value = programme.attrib[attr]
            new_value = shift_to_myt(old_value)
            if new_value != old_value:
                programme.attrib[attr] = new_value
                converted_count += 1

    return converted_count


def main():
    log("=" * 60)
    log("Refreshing EPG – converting all times to MYT (+0800)")
    log("=" * 60)

    xml_text = fetch_source_epg()
    root = ET.fromstring(xml_text.encode("utf-8"))

    # Set global <tv> date to current MYT
    now_myt = datetime.now(MYT)
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
    log(f"Times converted: {converted_count}")
    log(f"Output         : {OUTPUT_FILE}")
    log("=" * 60)


if __name__ == "__main__":
    main()
