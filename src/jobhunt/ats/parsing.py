"""Shared by the adapters: parse postings one at a time so one bad record can't sink a board."""

import logging
from collections.abc import Callable, Iterable
from typing import Any

from jobhunt.models import Job

log = logging.getLogger(__name__)


def parse_each(raws: Iterable[dict[str, Any]], parse: Callable[[dict[str, Any]], Job]) -> list[Job]:
    """Parse every posting; skip (and log) any that is malformed instead of failing them all.

    If one posting's error escaped, crawl() would mark the whole board failed on every run, and
    a failed board never closes its vanished jobs — so they would stay open forever.
    """
    jobs: list[Job] = []
    for raw in raws:
        try:
            jobs.append(parse(raw))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            log.warning("skipping malformed posting %r: %s: %s", raw.get("id"), type(e).__name__, e)
    return jobs
