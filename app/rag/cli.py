from __future__ import annotations

import argparse
import json

from app.config import AppConfig
from app.rag.service import RagService


def _dump_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def cmd_ingest(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    service = RagService(config)
    result = service.ingest_source(
        file_path=args.file,
        title=args.title,
        source_type=args.source_type.upper(),
    )
    _dump_json(result)
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    service = RagService(config)
    result = service.ingest_source(source_id=args.source_id)
    _dump_json(result)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    service = RagService(config)
    result = service.inspect_source(args.source_id)
    _dump_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--file", required=True)
    ingest_parser.add_argument("--title", required=True)
    ingest_parser.add_argument("--source-type", default="pptx")
    ingest_parser.set_defaults(func=cmd_ingest)

    reindex_parser = subparsers.add_parser("reindex")
    reindex_parser.add_argument("--source-id", required=True, type=int)
    reindex_parser.set_defaults(func=cmd_reindex)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--source-id", required=True, type=int)
    inspect_parser.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
