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
    ("Remote - US", "US", L.remote_needs_visa),
    ("Remote, United States", "US", L.remote_needs_visa),
    ("Remote - Germany", "DE", L.remote_europe_country),
    ("Berlin, Germany", "DE", L.other),
    ("New York, NY", "US", L.other),
    ("", None, L.other),
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


@pytest.mark.parametrize(
    "raw",
    [
        "Remote, AMER",
        "Distributed, AMER",
        "Remote - Seoul, South Korea",
        "Remote - São Paulo",
        "Remote-Jakarta",
        "Remote, KSA",
        "Ontario - Remote",
        "Remote - Mountain View",
        "Remote - Europe (Dubai)",
    ],
)
def test_remote_with_unknown_place_is_other(raw: str) -> None:
    assert classify(raw).location_class == L.other


@pytest.mark.parametrize(
    "raw",
    [
        "Home Based - Worldwide",
        "Remote, Global",
        "Distributed, Global",
        "Remote Based",
        "Distributed",
        "Remote ",
        "Fully Remote",
    ],
)
def test_only_global_words_is_remote_global(raw: str) -> None:
    assert classify(raw).location_class == L.remote_global


@pytest.mark.parametrize(
    "raw",
    [
        "Remote in Europe",
        "Remote-friendly (Europe)",
        "Remote across Europe",
        "Remote within the EU",
        "Remote - Europe (Iaşi welcome)",
    ],
)
def test_remote_europe_phrasings(raw: str) -> None:
    assert classify(raw).location_class in (L.remote_europe, L.remote_romania)


def test_cedilla_spellings_are_romania() -> None:
    assert classify("Iaşi, România").location_class == L.romania
    assert classify("Timişoara").location_class == L.romania


@pytest.mark.parametrize(
    ("raw", "country", "cls"),
    [
        ("Remote Germany", "DE", L.remote_europe_country),
        ("Germany (Remote)", "DE", L.remote_europe_country),
        ("Remote, Poland", "PL", L.remote_europe_country),
        ("Remote - Vienna, Austria", "AT", L.remote_europe_country),
        ("Remote (Sweden)", "SE", L.remote_europe_country),
        ("Remote - Zurich", "CH", L.remote_europe_country),
        ("Remote - London, UK", "GB", L.remote_needs_visa),
        ("Remote - Manchester", "GB", L.remote_needs_visa),
        ("Remote - Hybrid (UK)", "GB", L.remote_needs_visa),
        ("Remote - Hybrid (Germany)", "DE", L.remote_europe_country),
        ("Remote - Belgrade, Serbia", "RS", L.remote_europe_country),
        ("Remote - Kyiv, Ukraine", "UA", L.remote_europe_country),
        ("Remote - Toronto", "CA", L.other),
        ("Berlin, Germany", "DE", L.other),
        ("Remote, United States", "US", L.remote_needs_visa),
        ("New York, NY", "US", L.other),
    ],
)
def test_remote_in_one_european_country(raw: str, country: str, cls: L) -> None:
    office = classify(raw)
    assert (office.country, office.location_class) == (country, cls)


@pytest.mark.parametrize(
    "raw",
    ["Arad", "Deva", "Tulcea", "Târgu Mureș", "Baia Mare", "Pitești", "Alba Iulia", "Suceava"],
)
def test_romanian_county_seats(raw: str) -> None:
    assert classify(raw).location_class == L.romania
