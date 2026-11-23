import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from jobhunt import db
from jobhunt.apply.policy import romanian_today
from jobhunt.apply.run import chromium_page, run_apply
from jobhunt.crawl import crawl
from jobhunt.http import FetchError, PoliteClient
from jobhunt.mcp_server import mcp
from jobhunt.profile import ProfileError, load_profile, profile_dir
from jobhunt.seed.discover import discover
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
    commands.add_parser(
        "apply",
        help="fill queued applications; submits only with JOBHUNT_SUBMIT=1, else dry run",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "crawl":
        return asyncio.run(_crawl())
    if args.command == "discover":
        return asyncio.run(_discover(args.companies))
    if args.command == "apply":
        return asyncio.run(_apply())
    if args.command == "serve":
        mcp.run("stdio")
        return 0
    return 2


async def _discover(path: Path) -> int:
    companies = load_companies(path)
    conn = db.connect(db.db_path())
    found = 0
    needs_adapter: dict[str, list[str]] = {}
    async with PoliteClient() as client:
        for company in companies:
            db.upsert_company(conn, company)
            try:
                result = await discover(client, company)
            except FetchError as e:
                print(f"  {company.name}: error, try again later ({e})")
                continue
            if result.board is None:
                if result.needs_adapter:
                    needs_adapter.setdefault(result.needs_adapter, []).append(company.name)
                    print(f"  {company.name}: uses {result.needs_adapter} (no adapter yet)")
                else:
                    print(f"  {company.name}: no supported board found")
                continue
            db.upsert_board(conn, result.board)
            found += 1
            print(f"  {company.name} -> {result.board.key}")
    print(f"boards found: {found} of {len(companies)} companies")
    for system, names in sorted(needs_adapter.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"needs adapter: {system} {len(names)} ({', '.join(names)})")
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


async def _apply() -> int:
    try:
        profile = load_profile(profile_dir())
        cap = int(os.environ.get("JOBHUNT_DAILY_CAP", "10"))
    except (ProfileError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    submit = os.environ.get("JOBHUNT_SUBMIT") == "1"
    conn = db.connect(db.db_path())
    try:
        report = await run_apply(
            conn,
            today=romanian_today(),
            submit_enabled=submit,
            cap=cap,
            browser_factory=chromium_page,
            profile=profile,
        )
    finally:
        conn.close()
    print(f"submit is {'on' if submit else 'off'}, daily cap {cap}")
    print(
        f"submitted: {len(report.submitted)}  dry_run: {len(report.dry_run)}  "
        f"unconfirmed: {len(report.unconfirmed)}  manual: {len(report.manual)}  "
        f"waiting: {len(report.waiting)}"
    )
    for label, ids in (
        ("submitted", report.submitted),
        ("dry_run", report.dry_run),
        ("unconfirmed", report.unconfirmed),
        ("waiting", report.waiting),
    ):
        for job_id in ids:
            print(f"  {label}: {job_id}")
    for job_id, reason in report.manual.items():
        print(f"  manual: {job_id} ({reason})")
    return 0
