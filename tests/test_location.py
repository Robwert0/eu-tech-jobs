import pytest

from jobhunt.enums import LocationClass as L
from jobhunt.location import classify, keep

CASES = [
    ("Bucharest, Romania", "RO", L.romania),
    ("București", "RO", L.romania),
    ("Iași, Romania", "RO", L.romania),
    ("Cluj-Napoca", "RO", L.romania),
    ("Timisoara, RO", "RO", L.romania),
    ("Hybrid - Bucharest", "RO", L.romania),
    ("Remote - Romania", "RO", L.remote_romania),
    ("Remote, EMEA", None, L.remote_europe),
    ("Remote (Europe)", None, L.remote_europe),
    ("Remote - EU", None, L.remote_europe),
    ("Remote", None, L.remote_global),
    ("Anywhere", None, L.remote_global),
    ("Worldwide (Remote)", None, L.remote_global),
    ("Remote - US", "US", L.other),
    ("Remote, United States", "US", L.other),
    ("Remote - Germany", "DE", L.other),
    ("Berlin, Germany", "DE", L.other),
    ("New York, NY", "US", L.other),
    ("", None, L.other),
    # Beyond the plan's table: remote locked to a non-European region is not global.
    ("Remote - LATAM", None, L.other),
    ("Remote, APAC", None, L.other),
]


@pytest.mark.parametrize(("raw", "country", "cls"), CASES)
def test_classify_cases(raw: str, country: str | None, cls: L) -> None:
    office = classify(raw)
    assert office.raw == raw
    assert (office.country, office.location_class) == (country, cls)


def test_keep_any_kept_office_wins() -> None:
    assert keep([classify("New York, NY"), classify("Bucharest, Romania")]) is True
    assert keep([classify("New York, NY"), classify("Berlin, Germany")]) is False
    assert keep([]) is False
