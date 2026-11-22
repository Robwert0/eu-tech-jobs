import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from jobhunt import db
from jobhunt.crawl import crawl
from jobhunt.http import FetchError, PoliteClient
from jobhunt.mcp_server import mcp
from jobhunt.seed.discover import discover_board
from jobhunt.seed.manual import load_companies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobhunt", description="Find tech jobs workable from Romania."
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("crawl", help="fetch all known boards and store new jobs")
    discover = commands.add_parser("discover", help="find job boards for companies.json")
    discover.add_argument("--companies", type=Path, default=Path("companies.json"))
    commands.add_parser("serve", help="run the MCP server over stdio")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "crawl":
        return asyncio.run(_crawl())
    if args.command == "discover":
        return asyncio.run(_discover(args.companies))
    if args.command == "serve":
        mcp.run("stdio")
        return 0
    return 2


async def _discover(path: Path) -> int:
    companies = load_companies(path)
    conn = db.connect(db.db_path())
    found = 0
    async with PoliteClient() as client:
        for company in companies:
            db.upsert_company(conn, company)
            try:
                board = await discover_board(client, company)
            except FetchError as e:
                print(f"  {company.name}: error, try again later ({e})")
                continue
            if board is None:
                print(f"  {company.name}: no Greenhouse/Lever/Ashby board found")
                continue
            db.upsert_board(conn, board)
            found += 1
            print(f"  {company.name} -> {board.key}")
    print(f"boards found: {found} of {len(companies)} companies")
    return 0


async def _crawl() -> int:
    conn = db.connect(db.db_path())
    async with PoliteClient() as client:
        report = await crawl(conn, client, now=datetime.now(UTC))
    print(f"boards ok: {report.boards_ok}")
    if report.boards_failed:
        for board in report.boards_failed:
            print(f"failed: {board}")
    return 0
