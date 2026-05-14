from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DBConfig
from .loader import init_db, load_to_db
from .parser import parse_workbook


def _report_from_parse(parsed) -> dict:
    return {
        "source_file": parsed.source_file,
        "sheet_name": parsed.sheet_name,
        "model_code": parsed.model_code,
        "model_name": parsed.model_name,
        "rows_read": parsed.rows_read,
        "profiles": len(parsed.profiles),
        "systems": len(parsed.systems),
        "entitlements": len(parsed.entitlements),
        "accesses": parsed.access_count,
        "justifications": parsed.justifications_count,
        "errors_count": parsed.errors_count,
        "warnings_count": parsed.warnings_count,
        "issues": [
            {
                "severity": issue.severity,
                "stage": issue.stage,
                "sheet_name": issue.sheet_name,
                "source_row": issue.source_row,
                "source_col": issue.source_col,
                "error_code": issue.error_code,
                "message": issue.message,
                "raw_value": issue.raw_value,
            }
            for issue in parsed.issues
        ],
    }


def cmd_db_init(_: argparse.Namespace) -> int:
    config = DBConfig.from_env()
    init_db(config)
    print("Database initialized.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    file_path = str(Path(args.file).expanduser().resolve())
    parsed = parse_workbook(file_path=file_path, requested_sheet=args.sheet)
    print(json.dumps(_report_from_parse(parsed), ensure_ascii=False, indent=2))
    return 1 if parsed.errors_count > 0 else 0


def cmd_load(args: argparse.Namespace) -> int:
    file_path = str(Path(args.file).expanduser().resolve())
    parsed = parse_workbook(file_path=file_path, requested_sheet=args.sheet)
    config = DBConfig.from_env()
    load_report = load_to_db(config=config, parsed=parsed, snapshot_label=args.snapshot_label)
    output = {
        "load": load_report,
        "parse": {
            "errors_count": parsed.errors_count,
            "warnings_count": parsed.warnings_count,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETL for RoleModel matrix Excel -> PostgreSQL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_init_parser = subparsers.add_parser("db.init", help="Initialize PostgreSQL schema")
    db_init_parser.set_defaults(func=cmd_db_init)

    validate_parser = subparsers.add_parser("validate", help="Validate Excel source file")
    validate_parser.add_argument("--file", required=True, help="Path to source Excel file")
    validate_parser.add_argument(
        "--sheet",
        required=False,
        help="Optional sheet name. If omitted, 'Полная форма' is used when present, otherwise first sheet.",
    )
    validate_parser.set_defaults(func=cmd_validate)

    load_parser = subparsers.add_parser("load", help="Load Excel data into PostgreSQL")
    load_parser.add_argument("--file", required=True, help="Path to source Excel file")
    load_parser.add_argument(
        "--sheet",
        required=False,
        help="Optional sheet name. If omitted, 'Полная форма' is used when present, otherwise first sheet.",
    )
    load_parser.add_argument("--snapshot-label", required=False, help="Optional snapshot label")
    load_parser.set_defaults(func=cmd_load)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

