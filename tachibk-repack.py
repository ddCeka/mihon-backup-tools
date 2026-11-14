#!/usr/bin/env python3

import argparse
import gzip
import os
import shutil
import sys


def get_term_width():
    return min(shutil.get_terminal_size((48, 24)).columns, 60)


def short(text, max_len=36):
    text = str(text).strip()
    return text if len(text) <= max_len else "..." + text[-(max_len - 3) :]


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


def validate_proto(buf):
    """Performs a structural wire-format check and counts top-level manga entries."""
    pos = 0
    end = len(buf)
    manga_count = 0

    while pos < end:
        tag, pos = read_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if field_num == 0:
            raise ValueError(f"Invalid field number 0 at byte {pos}")

        if wire_type == 0:  # Varint
            _, pos = read_varint(buf, pos)
        elif wire_type == 1:  # 64-bit
            if pos + 8 > end:
                raise ValueError("Truncated 64-bit field")
            pos += 8
        elif wire_type == 2:  # Length-delimited
            length, pos = read_varint(buf, pos)
            if pos + length > end:
                raise ValueError(f"Truncated field #{field_num} (need {length}B)")
            if field_num == 1:
                manga_count += 1
            pos += length
        elif wire_type == 5:  # 32-bit
            if pos + 4 > end:
                raise ValueError("Truncated 32-bit field")
            pos += 4
        else:
            raise ValueError(f"Invalid wire type {wire_type} at byte {pos}")

    return manga_count


def main():
    parser = argparse.ArgumentParser(
        description="Validate and repack a .proto file back into a .tachibk backup."
    )
    parser.add_argument("proto_file", help="Path to the modified .proto file")
    parser.add_argument(
        "-o",
        "--output",
        help="Output .tachibk path (default: <name>_repacked.tachibk)",
        default=None,
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip protobuf wire-format validation before compressing",
    )
    args = parser.parse_args()

    w = get_term_width()
    line = "-" * w

    if not os.path.isfile(args.proto_file):
        print(f"[!] File not found: {args.proto_file}")
        sys.exit(1)

    with open(args.proto_file, "rb") as f:
        raw_data = f.read()

    if not raw_data:
        print("[!] Input .proto file is empty.")
        sys.exit(1)

    manga_count = None
    if not args.skip_check:
        try:
            manga_count = validate_proto(raw_data)
        except ValueError as e:
            print(f"\n{line}")
            print("[!] PROTO VALIDATION FAILED")
            print(f"    {e}")
            print("    Aborting so you don't restore a corrupt backup.")
            print(f"{line}\n")
            sys.exit(1)

    if args.output:
        out_path = args.output
    else:
        base = args.proto_file
        if base.endswith(".tachibk.proto"):
            base = base[: -len(".tachibk.proto")]
        elif base.endswith(".proto"):
            base = base[: -len(".proto")]
        out_path = f"{base}_repacked.tachibk"

    # Compress using standard GZIP (mtime=0 for clean, reproducible output)
    with open(out_path, "wb") as f_out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f_out, mtime=0) as gz:
            gz.write(raw_data)

    raw_kb = len(raw_data) / 1024
    gz_kb = os.path.getsize(out_path) / 1024

    print(f"\n{line}")
    print(" REPACK COMPLETE")
    if manga_count is not None:
        print(f" Entries : {manga_count} manga verified")
    print(f" Size    : {raw_kb:.1f} KB -> {gz_kb:.1f} KB")
    print(f" Output  : {short(out_path, w - 11)}")
    print(f"{line}\n")


if __name__ == "__main__":
    main()
