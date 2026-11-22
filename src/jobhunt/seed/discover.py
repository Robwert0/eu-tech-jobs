import re
import unicodedata
from collections.abc import Mapping
from html.parser import HTMLParser

from pydantic import BaseModel

from jobhunt.ats.base import ADAPTERS, AtsAdapter, detect_board, detect_system
from jobhunt.enums import Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.models import Board, Company


def slug_candidates(name: str) -> list[str]:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = text.split()
    joined = "".join(words)
    hyphenated = "-".join(words)
    return [joined] if joined == hyphenated else [joined, hyphenated]


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.links.append(value)


def _links(html: str) -> list[str]:
    collector = _LinkCollector()
    collector.feed(html)
    collector.close()
    return collector.links


class Discovery(BaseModel):
    board: Board | None
    needs_adapter: str | None = None


async def discover(
    client: PoliteClient,
    company: Company,
    *,
    adapters: Mapping[Ats, AtsAdapter] = ADAPTERS,
) -> Discovery:
    if company.board is not None:
        return Discovery(board=company.board)
    system: str | None = None
    if company.careers_url:
        try:
            final_url, page = await client.get_page(company.careers_url)
        except FetchError:
            final_url, page = company.careers_url, ""
        links = [final_url, *_links(page)]
        for link in links:
            board = detect_board(link, company.name)
            if board is not None:
                return Discovery(board=board)
        system = next((s for s in map(detect_system, links) if s), None)
    for slug in slug_candidates(company.name):
        for adapter in adapters.values():
            if not adapter.slug_guessable:
                continue
            found = await adapter.probe(client, slug)
            if found is not None:
                return Discovery(board=found.model_copy(update={"company": company.name}))
    return Discovery(board=None, needs_adapter=system)


async def discover_board(
    client: PoliteClient,
    company: Company,
    *,
    adapters: Mapping[Ats, AtsAdapter] = ADAPTERS,
) -> Board | None:
    return (await discover(client, company, adapters=adapters)).board
