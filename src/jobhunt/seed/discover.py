import re
import unicodedata
from collections.abc import Mapping
from html.parser import HTMLParser

from jobhunt.ats.base import ADAPTERS, AtsAdapter, detect_board
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


async def discover_board(
    client: PoliteClient,
    company: Company,
    *,
    adapters: Mapping[Ats, AtsAdapter] = ADAPTERS,
) -> Board | None:
    if company.board is not None:  # configured explicitly: nothing to discover
        return company.board
    if company.careers_url:
        try:
            page = await client.get_text(company.careers_url)
        except FetchError:
            page = ""
        for link in _links(page):
            board = detect_board(link, company.name)
            if board is not None:
                return board
    for slug in slug_candidates(company.name):
        for adapter in adapters.values():
            if not adapter.slug_guessable:
                continue
            board = await adapter.probe(client, slug)
            if board is not None:
                return board.model_copy(update={"company": company.name})

    return None
