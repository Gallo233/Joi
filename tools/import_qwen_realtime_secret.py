#!/usr/bin/env python3
"""Import an Alibaba Model Studio realtime key into Joi's secure store.

The CSV contents never enter config, stdout, logs, RPC state, or the Shell.
Both the Alibaba console's vertical export and a conventional header row are
accepted. On macOS the managed backend is the login Keychain.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import stat
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_companion.core.secret_store import QWEN_REALTIME_API_KEY_ENV, store_managed_secret


def read_qwen_api_key(path: Path) -> str:
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    vertical: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2:
            vertical[_label(row[0])] = str(row[1]).strip()
    key = vertical.get("apikey", "")
    if not key and rows:
        headers = [_label(value) for value in rows[0]]
        if "apikey" in headers and len(rows) >= 2:
            index = headers.index("apikey")
            if index < len(rows[1]):
                key = str(rows[1][index]).strip()
    if len(key) < 8 or len(key) > 4096 or any(char in key for char in ("\n", "\r", "\0")):
        raise ValueError("qwen_api_key_missing_or_invalid")
    return key


def import_qwen_api_key(path: Path, *, protect_source: bool = True) -> tuple[bool, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size > 256 * 1024:
        return False, "qwen_key_csv_invalid"
    try:
        key = read_qwen_api_key(resolved)
    except (OSError, UnicodeError, csv.Error, ValueError):
        return False, "qwen_key_csv_invalid"
    ok, error = store_managed_secret(QWEN_REALTIME_API_KEY_ENV, key)
    key = ""
    if not ok:
        return False, error or "secure_store_failed"
    if protect_source:
        try:
            resolved.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            return False, "source_permissions_failed"
    return True, ""


def _label(value: object) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a Qwen Realtime key into Joi's secure store.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--keep-source-mode", action="store_true")
    args = parser.parse_args()
    ok, error = import_qwen_api_key(args.csv_path, protect_source=not args.keep_source_mode)
    if ok:
        print("qwen_realtime_secret_imported")
        return 0
    print(error or "qwen_realtime_secret_import_failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
