# Mihon Backup Tools (`.tachibk` Editor)

A lightweight, zero-dependency Python CLI toolkit for inspecting, deduplicating, editing, and repacking Mihon / Tachiyomi `.tachibk` backup files. Designed with a compact terminal UI optimized for mobile environments like **Termux** as well as desktop terminals.

## Features

* **Zero External Dependencies:** Uses only the Python 3 standard library (`gzip`, `json`, `struct`, `argparse`, etc.)—no `protoc` compiler or `.proto` schema definitions required.
* **Backup Inspection & Extraction:** Decompresses `.tachibk` files into raw `.proto` binaries and human-readable `.json` library dumps.
* **Duplicate Chapter Detection:** Automatically scans your library for duplicate chapters by URL, chapter name + scanlator group, or chapter number + fetch date.
* **Date Filtering:** Pinpoints chapters fetched or uploaded around a specific target date (`±1` day window).
* **Safe Non-Destructive Patching:** Compiles edited `.json` files back into valid `.proto` binaries while preserving all unedited protobuf wire fields and unknown metadata.
* **Bulk Cleanup & Deduplication:** Automatically drops duplicate chapters or strips entire manga entries by title regex or source ID during compilation.
* **Wire-Format Validation:** Verifies protobuf structural integrity before compressing back into a `.tachibk` archive to prevent corrupted restores.

---

## Installation

1. **Prerequisites:** Ensure you have **Python 3.6+** installed. No external `pip` packages are needed.
2. **Clone the repository:**
   ```bash
   git clone https://github.com
   cd mihon-backup-tools
   ```
3. *(Optional)* Make the scripts executable on Linux/Termux/macOS:
   ```bash
   chmod +x tachibk-unpack.py json-to-proto.py tachibk-repack.py
   ```

---

## Toolkit Overview

This repository provides three standalone CLI scripts forming a complete round-trip workflow:

1. **`tachibk-unpack.py`** — Unpacks a `.tachibk` backup into `.proto` and `.json`, scans for duplicate chapters, and filters by date.
2. **`json-to-proto.py`** — Compiles an edited `.json` file back into a `.proto` (and optionally `.tachibk`), with built-in deduplication and filtering flags.
3. **`tachibk-repack.py`** — Validates a raw `.proto` file's wire format and compresses it into a clean, reproducible `.tachibk` backup.

---

## Usage

### 1. Unpack & Inspect a Backup (`tachibk-unpack.py`)

Extract a `.tachibk` file into `<backup>.tachibk.proto` and `<backup>.tachibk.json` while generating a duplicate report in the terminal:

```bash
python3 tachibk-unpack.py backup.tachibk
```

You will be prompted to enter an optional date filter (`YYYY-MM-DD`), or you can pass it directly via CLI flag:

```bash
python3 tachibk-unpack.py backup.tachibk -d 2026-05-15
```

| Flag | Description |
| :--- | :--- |
| `-d`, `--date` | Filter and highlight chapters fetched/uploaded within `±1` day of `YYYY-MM-DD`. |

---

### 2. Edit & Compile JSON back to Proto/Backup (`json-to-proto.py`)

After editing the generated `.json` file (or to run automated cleanup/deduplication rules), compile your changes back into a `.proto` file:

```bash
python3 json-to-proto.py backup.tachibk.json --pack
```

#### Compilation Options

| Flag | Description |
| :--- | :--- |
| `-p`, `--proto` | Path to the original `.proto` file (auto-detected if named `<name>.proto` alongside the JSON). |
| `-o`, `--output` | Custom output `.proto` path (default: `<name>_edited.proto`). |
| `--pack` | Also compress the compiled output directly into `<name>_edited.tachibk`. |
| `--dedup {url,name,num_date}` | Automatically drop duplicate chapters by `url`, `name` (+ scanlator), or `num_date` (chapter number + fetch date). |
| `--remove-title PATTERN` | Remove manga matching a regex/text pattern (case-insensitive, repeatable). |
| `--remove-source ID` | Remove all manga belonging to a specific numeric source ID (repeatable). |
| `--dry-run` | Preview manga/chapter counts and removals without writing any files. |

#### Examples

```bash
# Preview deduplicating chapters by URL and packing directly into .tachibk
python3 json-to-proto.py backup.tachibk.json --dedup url --pack --dry-run

# Remove specific titles and a dead source ID, then compile and pack
python3 json-to-proto.py backup.tachibk.json --remove-title "Test Manga" --remove-source 123456789 --pack
```

---

### 3. Validate & Repack a `.proto` File (`tachibk-repack.py`)

If you modified a `.proto` file separately or compiled without `--pack`, validate its protobuf wire structure and compress it into a `.tachibk` file:

```bash
python3 tachibk-repack.py backup_edited.proto
```

| Flag | Description |
| :--- | :--- |
| `-o`, `--output` | Custom output `.tachibk` path (default: `<name>_repacked.tachibk`). |
| `--skip-check` | Skip protobuf wire-format validation before GZIP compression. |

---

## Typical Workflow

```bash
# Step 1: Unpack your backup and inspect duplicates
python3 tachibk-unpack.py my_backup.tachibk -d 2026-11-01

# Step 2: (Optional) Manually edit my_backup.tachibk.json in any text editor

# Step 3: Compile changes, auto-deduplicate chapters, and pack into a new .tachibk
python3 json-to-proto.py my_backup.tachibk.json --dedup url --pack

# Step 4: Restore my_backup_edited.tachibk in Mihon / Tachiyomi
```

---

## JSON Structure & Editable Fields

`tachibk-unpack.py` exports your library into an array of manga objects. When compiling back with `json-to-proto.py`, you can safely delete entire manga objects, remove items from `"chapters"`, or edit the supported fields listed below.

```json
[
  {
    "backup_index": 1,
    "source_id": 2499283573021220255,
    "title": "Example Manga Title",
    "url": "/manga/example-manga",
    "chapter_count": 2,
    "chapters": [
      {
        "name": "Chapter 1",
        "chapter_number": 1.0,
        "scanlator": "Example Scans",
        "url": "/chapter/example-ch-1",
        "date_fetch_ms": 1700000000000,
        "date_fetch_date": "2023-11-14",
        "date_fetch_short": "2023-11-14 22:13",
        "date_fetch_iso": "2023-11-14T22:13:20",
        "date_upload_ms": 1699900000000,
        "date_upload_short": "2023-11-13 18:26",
        "date_upload_iso": "2023-11-13T18:26:40"
      }
    ]
  }
]
```

### Supported Fields for Manual Editing

* **Manga Level:**
  * `source_id` *(int)* — Numeric extension/source ID (useful for migrating entries between sources).
  * `title` *(string)* — Display title of the manga.
  * `url` *(string)* — Relative source URL path for the manga.
  * *(Note: Keep `backup_index` intact so `json-to-proto.py` can match each entry back to its original binary record and preserve read status, categories, and trackers.)*
* **Chapter Level:**
  * `name` *(string)* — Chapter title.
  * `chapter_number` *(float)* — Numeric chapter number.
  * `scanlator` *(string)* — Scanlation group name (set to `""` to clear the field).
  * `url` *(string)* — Relative source URL path for the chapter.
  * `date_fetch_ms` / `date_upload_ms` *(int)* — Unix timestamps in milliseconds (the `*_date`, `*_short`, and `*_iso` fields are read-only helpers for human inspection).

---

## Safety Notes

* **Always keep a backup of your original `.tachibk` file** before restoring a modified archive.
* `json-to-proto.py` preserves unrecognized protobuf fields (reading progress, categories, tracking, etc.) by patching only modified fields in-place on top of the original binary records.

---

## Contributing

Contributions, bug reports, and feature requests are welcome. Feel free to open an issue or submit a pull request on [GitHub](https://github.com/ddCeka/mihon-backup-tools).

## License

This project is licensed under the [MIT License](LICENSE).
