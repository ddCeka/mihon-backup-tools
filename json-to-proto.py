#!/usr/bin/env python3

import argparse
import gzip
import json
import os
import re
import shutil
import struct
import sys


def get_term_width():
    return min(shutil.get_terminal_size((48, 24)).columns, 60)


def short(text, max_len=36):
    text = str(text).strip()
    return text if len(text) <= max_len else "..." + text[-(max_len - 3) :]


def encode_varint(val):
    if val < 0:
        val += 1 << 64
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def read_varint(buf, pos):
    val = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("Unexpected EOF while reading varint")
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7


def parse_proto_ordered(buf, start=0, end=None):
    """Returns a list of (field_num, wire_type, raw_val) preserving field order."""
    if end is None:
        end = len(buf)
    records = []
    pos = start
    while pos < end:
        tag, pos = read_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:
            val, pos = read_varint(buf, pos)
        elif wire_type == 1:
            val = buf[pos : pos + 8]
            pos += 8
        elif wire_type == 2:
            length, pos = read_varint(buf, pos)
            val = buf[pos : pos + length]
            pos += length
        elif wire_type == 5:
            val = buf[pos : pos + 4]
            pos += 4
        else:
            raise ValueError(f"Unsupported wire type {wire_type} at pos {pos}")

        records.append((field_num, wire_type, val))
    return records


def serialize_proto(records):
    out = bytearray()
    for field_num, wire_type, val in records:
        out.extend(encode_varint((field_num << 3) | wire_type))
        if wire_type == 0:
            out.extend(encode_varint(val))
        elif wire_type in (1, 5):
            out.extend(val)
        elif wire_type == 2:
            out.extend(encode_varint(len(val)))
            out.extend(val)
    return bytes(out)


def set_field(records, target_field, wire_type, new_val):
    """Updates an existing field in-place or appends it if missing."""
    updated = False
    new_records = []
    for f_num, w_type, val in records:
        if f_num == target_field and not updated:
            new_records.append((f_num, wire_type, new_val))
            updated = True
        elif f_num != target_field:
            new_records.append((f_num, w_type, val))
    if not updated:
        new_records.append((target_field, wire_type, new_val))
    return new_records


def patch_chapter(orig_ch_bytes, ch_json):
    recs = parse_proto_ordered(orig_ch_bytes) if orig_ch_bytes else []
    if "url" in ch_json:
        recs = set_field(recs, 1, 2, ch_json["url"].encode("utf-8"))
    if "name" in ch_json:
        recs = set_field(recs, 2, 2, ch_json["name"].encode("utf-8"))
    if "scanlator" in ch_json:
        if ch_json["scanlator"]:
            recs = set_field(recs, 3, 2, ch_json["scanlator"].encode("utf-8"))
        else:
            recs = [(f, w, v) for f, w, v in recs if f != 3]
    if "date_fetch_ms" in ch_json:
        recs = set_field(recs, 7, 0, int(ch_json["date_fetch_ms"]))
    if "date_upload_ms" in ch_json:
        recs = set_field(recs, 8, 0, int(ch_json["date_upload_ms"]))
    if "chapter_number" in ch_json:
        packed_float = struct.pack("<f", float(ch_json["chapter_number"]))
        recs = set_field(recs, 9, 5, packed_float)
    return serialize_proto(recs)


def patch_manga(orig_manga_bytes, m_json, dedup_mode=None):
    recs = parse_proto_ordered(orig_manga_bytes) if orig_manga_bytes else []

    if m_json.get("source_id") is not None:
        recs = set_field(recs, 1, 0, int(m_json["source_id"]))
    if "url" in m_json:
        recs = set_field(recs, 2, 2, m_json["url"].encode("utf-8"))
    if "title" in m_json:
        recs = set_field(recs, 3, 2, m_json["title"].encode("utf-8"))

    orig_chapters = [
        val for f_num, w_type, val in recs if f_num == 16 and w_type == 2
    ]
    orig_by_url = {}
    for ch_b in orig_chapters:
        c_recs = parse_proto_ordered(ch_b)
        for f_num, w_type, val in c_recs:
            if f_num == 1 and w_type == 2:
                orig_by_url[val.decode("utf-8", errors="ignore")] = ch_b
                break

    json_chapters = m_json.get("chapters", [])
    seen_keys = set()
    rebuilt_chapters = []
    removed_dups = 0

    for idx, ch_j in enumerate(json_chapters):
        if dedup_mode == "url":
            key = ch_j.get("url", "")
        elif dedup_mode == "name":
            key = (ch_j.get("name", ""), ch_j.get("scanlator", ""))
        elif dedup_mode == "num_date":
            key = (
                ch_j.get("chapter_number", -1),
                ch_j.get("date_fetch_date", ""),
            )
        else:
            key = None

        if key is not None:
            if key in seen_keys:
                removed_dups += 1
                continue
            seen_keys.add(key)

        base_bytes = orig_by_url.get(ch_j.get("url", ""))
        if (
            base_bytes is None
            and len(json_chapters) == len(orig_chapters)
            and idx < len(orig_chapters)
        ):
            base_bytes = orig_chapters[idx]

        rebuilt_chapters.append((16, 2, patch_chapter(base_bytes, ch_j)))

    final_recs = []
    inserted = False
    for f, w, v in recs:
        if f == 16:
            if not inserted:
                final_recs.extend(rebuilt_chapters)
                inserted = True
        else:
            final_recs.append((f, w, v))
    if not inserted:
        final_recs.extend(rebuilt_chapters)

    return serialize_proto(final_recs), removed_dups


def main():
    parser = argparse.ArgumentParser(
        description="Compile edited .json back into .proto (and optionally .tachibk)."
    )
    parser.add_argument("json_file", help="Path to edited .json file")
    parser.add_argument(
        "-p",
        "--proto",
        help="Original .proto file (auto-detected if omitted)",
        default=None,
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output .proto path (default: <name>_edited.proto)",
        default=None,
    )
    parser.add_argument(
        "--pack",
        action="store_true",
        help="Also compress directly into a .tachibk file",
    )
    parser.add_argument(
        "--remove-title",
        action="append",
        default=[],
        help="Regex/text to remove matching manga titles (repeatable)",
    )
    parser.add_argument(
        "--remove-source",
        type=int,
        action="append",
        default=[],
        help="Source ID to remove completely (repeatable)",
    )
    parser.add_argument(
        "--dedup",
        choices=["url", "name", "num_date"],
        default=None,
        help="Auto-drop duplicate chapters by: url, name, or num_date",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing output files",
    )
    args = parser.parse_args()

    w = get_term_width()
    line = "-" * w

    proto_in = args.proto
    if not proto_in:
        if args.json_file.endswith(".json"):
            candidate = args.json_file[: -len(".json")] + ".proto"
            if os.path.isfile(candidate):
                proto_in = candidate

    if not proto_in or not os.path.isfile(proto_in):
        print("[!] Base .proto file not found. Pass it with -p <file.proto>")
        sys.exit(1)

    with open(args.json_file, "r", encoding="utf-8") as f:
        edited_library = json.load(f)

    with open(proto_in, "rb") as f:
        root_recs = parse_proto_ordered(f.read())

    orig_manga_list = [v for f, w_t, v in root_recs if f == 1 and w_t == 2]
    other_root_recs = [(f, w_t, v) for f, w_t, v in root_recs if f != 1]

    title_patterns = [
        re.compile(pat, re.IGNORECASE) for pat in args.remove_title
    ]
    source_blacklist = set(args.remove_source)

    kept_manga_recs = []
    removed_manga = 0
    total_dups_removed = 0
    total_chapters = 0

    for entry in edited_library:
        title = entry.get("title", "")
        src_id = entry.get("source_id")
        b_idx = entry.get("backup_index", 0)

        if src_id in source_blacklist or any(
            p.search(title) for p in title_patterns
        ):
            removed_manga += 1
            continue

        orig_blob = (
            orig_manga_list[b_idx - 1]
            if 1 <= b_idx <= len(orig_manga_list)
            else None
        )
        patched_blob, dups_dropped = patch_manga(
            orig_blob, entry, dedup_mode=args.dedup
        )
        total_dups_removed += dups_dropped
        total_chapters += len(entry.get("chapters", [])) - dups_dropped
        kept_manga_recs.append((1, 2, patched_blob))

    final_root_recs = kept_manga_recs + other_root_recs
    final_bytes = serialize_proto(final_root_recs)

    base = args.json_file
    for suffix in (".tachibk.json", ".json"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break

    proto_out = args.output or f"{base}_edited.proto"
    tachibk_out = f"{base}_edited.tachibk"

    print(f"{line}\n")
    print(" JSON -> PROTO COMPILER" + (" [DRY RUN]" if args.dry_run else ""))
    print(line)
    print(f" Manga   : {len(kept_manga_recs)} kept, {removed_manga} removed")
    print(
        f" Chapters: {total_chapters} kept, {total_dups_removed} dups dropped"
    )

    if not args.dry_run:
        with open(proto_out, "wb") as f:
            f.write(final_bytes)
        print(f" Proto   : {short(proto_out, w - 11)}")

        if args.pack:
            with open(tachibk_out, "wb") as f_out:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=f_out, mtime=0
                ) as gz:
                    gz.write(final_bytes)
            print(f" Backup  : {short(tachibk_out, w - 11)}")

    print(f"{line}\n")


if __name__ == "__main__":
    main()
