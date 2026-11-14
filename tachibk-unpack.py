#!/usr/bin/env python3

import argparse
from collections import defaultdict
import datetime
import gzip
import json
import shutil
import struct
import sys


def get_term_width():
    return min(shutil.get_terminal_size((48, 24)).columns, 60)


def short(text, max_len=36):
    if not text:
        return "-"
    text = str(text).strip()
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def read_varint(buf, pos):
    val = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7


def parse_proto(buf, start=0, end=None):
    if end is None:
        end = len(buf)
    fields = {}
    pos = start
    while pos < end:
        tag, pos = read_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # Varint
            val, pos = read_varint(buf, pos)
        elif wire_type == 1:  # 64-bit
            val = int.from_bytes(buf[pos : pos + 8], "little")
            pos += 8
        elif wire_type == 2:  # Length-delimited
            length, pos = read_varint(buf, pos)
            val = buf[pos : pos + length]
            pos += length
        elif wire_type == 5:  # 32-bit
            val = int.from_bytes(buf[pos : pos + 4], "little")
            pos += 4
        else:
            raise ValueError(f"Unsupported wire type {wire_type} at pos {pos}")

        fields.setdefault(field_num, []).append((wire_type, val))
    return fields


def decode_str(raw_bytes):
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def ms_to_iso(ms):
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000).isoformat()


def ms_to_short_dt(ms):
    if not ms:
        return "-"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def ms_to_date(ms):
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def parse_target_window(date_str):
    """Returns (target_date_str, start_ms, end_ms) with a +-1 day buffer, or (None, None, None)."""
    if not date_str:
        return None, None, None
    dt = datetime.datetime.strptime(date_str.strip(), "%Y-%m-%d")
    start_ms = int((dt - datetime.timedelta(days=1)).timestamp() * 1000)
    end_ms = int((dt + datetime.timedelta(days=2)).timestamp() * 1000)
    return dt.strftime("%Y-%m-%d"), start_ms, end_ms


def detect_duplicates(chapters):
    """Finds duplicate chapters by URL, (name + scanlator), or (chapter_number + fetch_date)."""
    by_url = defaultdict(list)
    by_name_scan = defaultdict(list)
    by_num_date = defaultdict(list)

    for idx, ch in enumerate(chapters):
        if ch["url"]:
            by_url[ch["url"]].append((idx, ch))
        by_name_scan[(ch["name"], ch["scanlator"])].append((idx, ch))
        if ch["chapter_number"] >= 0 and ch["date_fetch_date"]:
            by_num_date[(ch["chapter_number"], ch["date_fetch_date"])].append(
                (idx, ch)
            )

    issues = []

    for url, items in by_url.items():
        if len(items) > 1:
            issues.append(("Dup URL", short(url, 32), [c for _, c in items]))

    for (name, scanlator), items in by_name_scan.items():
        if len(items) > 1:
            issues.append((
                "Dup Name+Group",
                f"{short(name, 22)} [{short(scanlator, 12)}]",
                [c for _, c in items],
            ))

    for (num, fetch_date), items in by_num_date.items():
        if len(items) > 1:
            urls = {c["url"] for _, c in items}
            if len(urls) > 1:
                issues.append((
                    "Same Ch# & Date",
                    f"Ch.{num} ({fetch_date})",
                    [c for _, c in items],
                ))

    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Unpack .tachibk to .proto/.json and check duplicates (Termux compact UI)."
    )
    parser.add_argument("backup_file", help="Path to .tachibk file")
    parser.add_argument(
        "-d",
        "--date",
        help="Optional date filter (YYYY-MM-DD)",
        default=None,
    )
    args = parser.parse_args()

    w = get_term_width()
    line = "-" * w

    date_input = args.date
    if date_input is None:
        date_input = input("Filter date (YYYY-MM-DD, Enter=skip): ").strip()

    try:
        target_date_str, target_start_ms, target_end_ms = parse_target_window(
            date_input
        )
    except ValueError:
        print("[!] Invalid date. Use YYYY-MM-DD")
        sys.exit(1)

    with gzip.open(args.backup_file, "rb") as f:
        raw_data = f.read()

    proto_out = args.backup_file + ".proto"
    with open(proto_out, "wb") as f:
        f.write(raw_data)

    root = parse_proto(raw_data)
    manga_entries = root.get(1, [])

    parsed_library = []
    date_matches = []
    duplicate_reports = []

    for idx, (_, manga_bytes) in enumerate(manga_entries, start=1):
        m_fields = parse_proto(manga_bytes)
        source_id = m_fields.get(1, [(0, None)])
        url = decode_str(m_fields.get(2, [(2, b"")]))
        title = decode_str(m_fields.get(3, [(2, b"")]))

        chapters_raw = m_fields.get(16, [])
        chapters = []

        for _, ch_bytes in chapters_raw:
            c_fields = parse_proto(ch_bytes)
            ch_url = decode_str(c_fields.get(1, [(2, b"")]))
            ch_name = decode_str(c_fields.get(2, [(2, b"")]))
            scanlator = decode_str(c_fields.get(3, [(2, b"")]))
            date_fetch = c_fields.get(7, [(0, 0)])
            date_upload = c_fields.get(8, [(0, 0)])
            ch_num_raw = c_fields.get(9, [(5, 0)])
            ch_num = round(struct.unpack("<f", struct.pack("<I", ch_num_raw)), 3)

            ch_info = {
                "name": ch_name,
                "chapter_number": ch_num,
                "scanlator": scanlator,
                "url": ch_url,
                "date_fetch_ms": date_fetch,
                "date_fetch_date": ms_to_date(date_fetch),
                "date_fetch_short": ms_to_short_dt(date_fetch),
                "date_fetch_iso": ms_to_iso(date_fetch),
                "date_upload_ms": date_upload,
                "date_upload_short": ms_to_short_dt(date_upload),
                "date_upload_iso": ms_to_iso(date_upload),
            }
            chapters.append(ch_info)

            if target_start_ms is not None and (
                (target_start_ms <= date_fetch <= target_end_ms)
                or (target_start_ms <= date_upload <= target_end_ms)
            ):
                date_matches.append((idx, source_id, title, ch_info))

        dup_issues = detect_duplicates(chapters)
        if dup_issues:
            has_target_date = bool(
                target_date_str
                and any(
                    c["date_fetch_date"] == target_date_str
                    for _, _, ch_list in dup_issues
                    for c in ch_list
                )
            )
            duplicate_reports.append({
                "backup_index": idx,
                "source_id": source_id,
                "title": title,
                "matches_target_date": has_target_date,
                "issues": dup_issues,
            })

        parsed_library.append({
            "backup_index": idx,
            "source_id": source_id,
            "title": title,
            "url": url,
            "chapter_count": len(chapters),
            "chapters": chapters,
        })

    json_out = args.backup_file + ".json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(parsed_library, f, indent=2, ensure_ascii=False)

    print(f"\n{line}")
    print(f"Entries: {len(manga_entries)} | Duplicates: {len(duplicate_reports)}")
    print(f"Proto : {short(proto_out, w - 9)}")
    print(f"JSON  : {short(json_out, w - 9)}")
    print(line)

    print("[1] DUPLICATE CHAPTERS")
    if target_date_str:
        duplicate_reports.sort(key=lambda x: not x["matches_target_date"])

    if not duplicate_reports:
        print("  None found.")
    else:
        for report in duplicate_reports:
            star = " *MATCH*" if report["matches_target_date"] else ""
            print(f"\n#{report['backup_index']} {short(report['title'], w - 12)}{star}")
            for reason, detail, ch_list in report["issues"]:
                print(f"  ! {reason}: {detail}")
                for c in ch_list:
                    print(
                        f"    - {short(c['name'], 22)} | {c['date_fetch_short']}"
                    )
                    print(f"      {short(c['url'], w - 7)}")

    if target_date_str:
        print(f"\n{line}")
        print(f"[2] AROUND {target_date_str}")
        if not date_matches:
            print("  None found.")
        else:
            for idx, src, title, ch in date_matches:
                print(f"\n#{idx} {short(title, w - 6)}")
                print(
                    f"  {short(ch['name'], 22)} (#{ch['chapter_number']})"
                )
                print(
                    f"  F:{ch['date_fetch_short']} | U:{ch['date_upload_short']}"
                )

    print(f"\n{line}")


if __name__ == "__main__":
    main()
