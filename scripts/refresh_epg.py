#!/usr/bin/env python3
import gzip
import io
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "EPG" / "epg.xml"

LOCAL_EPG_URL = "https://raw.githubusercontent.com/mohdzufar/IPTV/refs/heads/main/EPG/epg.xml"

PLAYLIST_FILES = [
    REPO_ROOT / "Channels" / "Flatten.m3u8",
    REPO_ROOT / "Main.m3u8",
]

TARGET_TZ = timezone(timedelta(hours=8))
TARGET_OFFSET = "+0800"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

XMLTV_TIME_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?$")
URL_TVG_RE = re.compile(r'url-tvg="([^"]+)"')


def log(message):
    print(message, flush=True)


def get_epg_sources_from_playlist():
    env_sources = os.environ.get("EPG_SOURCES", "").strip()
    if env_sources:
        return [x.strip() for x in re.split(r"[\n,;]+", env_sources) if x.strip()]

    for playlist in PLAYLIST_FILES:
        if not playlist.exists():
            continue

        first_line = playlist.read_text(encoding="utf-8-sig", errors="replace").splitlines()[0]
        match = URL_TVG_RE.search(first_line)
        if match:
            return [x.strip() for x in match.group(1).split(",") if x.strip()]

    raise RuntimeError("No EPG source found in playlist url-tvg header.")


def fetch_url(url):
    log(f"Fetching: {url}")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/xml,text/xml,*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()

    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)

    return data.decode("utf-8-sig", errors="replace")


def parse_offset(offset_text):
    if not offset_text or offset_text == "Z":
        return timezone.utc

    sign = 1 if offset_text[0] == "+" else -1
    hours = int(offset_text[1:3])
    minutes = int(offset_text[3:5])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def convert_xmltv_time(value):
    value = value.strip()
    match = XMLTV_TIME_RE.match(value)
    if not match:
        return value, False

    timestamp, offset_text = match.groups()
    if len(timestamp) == 12:
        timestamp += "00"

    source_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S")

    # If no timezone exists, treat source as UTC, then convert to Malaysia time.
    source_tz = parse_offset(offset_text)
    converted = source_time.replace(tzinfo=source_tz).astimezone(TARGET_TZ)

    return f"{converted.strftime('%Y%m%d%H%M%S')} {TARGET_OFFSET}", True


def convert_programme_times(root):
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


def programme_key(programme):
    return (
        programme.get("channel", ""),
        programme.get("start", ""),
        programme.get("stop", ""),
        "".join(title.text or "" for title in programme.findall("title")),
    )


def merge_epg_sources(sources):
    merged = ET.Element(
        "tv",
        {
            "generator-info-name": "mohdzufar IPTV EPG +0800 converter",
            "generator-info-url": "https://github.com/mohdzufar/IPTV",
        },
    )

    seen_channels = set()
    seen_programmes = set()

    source_count = 0
    channel_count = 0
    programme_count = 0
    converted_count = 0

    for source in sources:
        xml_text = fetch_url(source)
        root = ET.fromstring(xml_text.encode("utf-8"))
        source_count += 1

        converted_count += convert_programme_times(root)

        for channel in root.findall("channel"):
            channel_id = channel.get("id") or ET.tostring(channel, encoding="unicode")
            if channel_id in seen_channels:
                continue

            seen_channels.add(channel_id)
            merged.append(channel)
            channel_count += 1

        for programme in root.findall("programme"):
            key = programme_key(programme)
            if key in seen_programmes:
                continue

            seen_programmes.add(key)
            merged.append(programme)
            programme_count += 1

    return merged, source_count, channel_count, programme_count, converted_count


def update_playlist_header(path):
    if not path.exists():
        return False

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        text = handle.read()

    lines = text.splitlines(keepends=True)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    new_header = f'#EXTM3U url-tvg="{LOCAL_EPG_URL}"{newline}'

    if lines and lines[0].startswith("#EXTM3U"):
        if lines[0] == new_header:
            return False
        lines[0] = new_header
    else:
        lines.insert(0, new_header)

    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))

    return True


def main():
    log("=" * 60)
    log("Refreshing EPG and converting XMLTV times to +0800")
    log("=" * 60)

    sources = get_epg_sources_from_playlist()
    merged, source_count, channel_count, programme_count, converted_count = merge_epg_sources(sources)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )

    with OUTPUT_FILE.open("ab") as handle:
        handle.write(b"\n")

    header_updates = 0
    for playlist in PLAYLIST_FILES:
        if update_playlist_header(playlist):
            header_updates += 1

    log(f"Sources loaded : {source_count}")
    log(f"Channels       : {channel_count}")
    log(f"Programmes     : {programme_count}")
    log(f"Times converted: {converted_count}")
    log(f"Header updates : {header_updates}")
    log(f"Output         : {OUTPUT_FILE}")
    log("=" * 60)


if __name__ == "__main__":
    main()
