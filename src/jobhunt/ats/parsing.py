import logging
from collections.abc import Callable, Iterable
from typing import Any

from jobhunt.models import Job

log = logging.getLogger(__name__)


def parse_each(raws: Iterable[dict[str, Any]], parse: Callable[[dict[str, Any]], Job]) -> list[Job]:
    jobs: list[Job] = []
    for raw in raws:
        try:
            jobs.append(parse(raw))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            log.warning("skipping malformed posting %r: %s: %s", raw.get("id"), type(e).__name__, e)
    return jobs
