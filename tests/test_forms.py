import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from playwright.async_api import Page, async_playwright

from jobhunt.apply.forms import (
    UnsupportedForm,
    custom_questions,
    greenhouse_fields,
    job_fields,
    read_form,
    standard_key,
)
from jobhunt.enums import Ats
from jobhunt.models import Board, FormField, Job

FORMS = Path(__file__).parent / "fixtures" / "forms"
GITLAB_QUESTIONS = "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs/8638232002"


def greenhouse_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FORMS / "greenhouse_questions.json").read_text())
    return data


def by_name(fields: list[FormField]) -> dict[str, FormField]:
    return {f.name: f for f in fields}


def make_job(ats: Ats, slug: str = "gitlab", external_id: str = "8638232002") -> Job:
    return Job(
        id=f"{ats}:{slug}:{external_id}",
        board=Board(company="Acme", ats=ats, slug=slug),
        external_id=external_id,
        title="Engineer",
        url="https://example.com/job",
        apply_url="https://example.com/job/apply",
        offices=[],
        description="",
        posted_at=None,
    )


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


def test_reads_required_fields_greenhouse() -> None:
    fields = by_name(greenhouse_fields(greenhouse_payload()))

    assert fields["first_name"] == FormField(
        name="first_name", label="First Name", type="text", required=True, options=[]
    )
    assert fields["resume"].type == "file"
    assert fields["resume"].required
    assert not fields["phone"].required
    sponsorship = fields["question_37369959002"]
    assert sponsorship.type == "select"
    assert sponsorship.required
    assert sponsorship.options == ["Yes", "No"]
    assert "resume_text" not in fields


async def test_reads_required_fields_lever(page: Page) -> None:
    await page.set_content((FORMS / "lever.html").read_text())

    fields = by_name(await read_form(page, Ats.lever))

    assert fields["name"] == FormField(
        name="name", label="Full name", type="text", required=True, options=[]
    )
    assert fields["email"].type == "email"
    assert fields["resume"].type == "file"
    assert fields["resume"].required
    assert fields["urls[LinkedIn]"].label == "LinkedIn URL"
    assert not fields["urls[LinkedIn]"].required
    assert "selectedLocation" not in fields

    authorised = fields["cards[1c719ca9-5069-4afe-9e82-39ca420e0edb][field0]"]
    assert authorised.type == "radio"
    assert authorised.required
    assert authorised.options == ["Yes", "No"]

    languages = fields["cards[a69a985a-eae9-4c14-90fb-b5a4b891523e][field0]"]
    assert languages.type == "checkbox"
    assert "English (ENG)" in languages.options

    heard = fields["cards[a6197d84-549e-4a91-8bb0-6af972510013][field0]"]
    assert heard.type == "select"
    assert heard.options
    assert "" not in heard.options

    why = fields["cards[ce72d538-c9ad-41f3-8e9a-618d40c82e3a][field1]"]
    assert why.label == "Why do you want to work at Palantir?"
    assert why.type == "textarea"
    assert not why.required


async def test_reads_required_fields_ashby(page: Page) -> None:
    await page.set_content((FORMS / "ashby.html").read_text())

    fields = by_name(await read_form(page, Ats.ashby))

    assert fields["_systemfield_name"] == FormField(
        name="_systemfield_name", label="Name", type="text", required=True, options=[]
    )
    assert fields["_systemfield_email"].type == "email"
    assert fields["_systemfield_resume"].type == "file"
    assert fields["_systemfield_resume"].required
    assert fields["1c843a13-4fe3-43df-afcf-37a69d88591b"].label == "Github"
    assert not fields["ca5c9d78-ec13-4bf2-86b8-bce6ff32e45e"].required
    assert fields["ce587bdf-31fa-4768-9e92-7741b7ad2317"].label == "Notice Period"

    timezone = fields["9fd0020d-14f0-47f3-b076-f31e5eaa8295"]
    assert timezone.type == "radio"
    assert timezone.options == ["Yes", "No"]
    assert timezone.required

    assert fields["7d3da580-9e6d-494f-a63b-0a889e0a157a"].type == "textarea"


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("First Name", "first_name"),
        ("Last Name", "last_name"),
        ("Name", "full_name"),
        ("Full name", "full_name"),
        ("Email", "email"),
        ("Phone", "phone"),
        ("LinkedIn Profile", "linkedin"),
        ("LinkedIn URL", "linkedin"),
        ("LinkedIn Profile URL", "linkedin"),
        ("GitHub Profile URL", "github"),
        ("Github", "github"),
        ("Resume/CV", "resume"),
        ("Resume", "resume"),
        ("Cover Letter", "cover_letter"),
        ("Current location", "location"),
        ("Current company", None),
        ("Why do you want to work at Palantir?", None),
    ],
)
def test_standard_fields_recognised(label: str, key: str | None) -> None:
    field = FormField(name="x", label=label, type="text", required=False, options=[])

    assert standard_key(field) == key


async def test_custom_questions_exclude_standard(page: Page) -> None:
    await page.set_content((FORMS / "lever.html").read_text())

    names = {f.name for f in custom_questions(await read_form(page, Ats.lever))}

    assert names.isdisjoint({"name", "email", "phone", "resume", "urls[LinkedIn]", "urls[GitHub]"})
    assert "cards[ce72d538-c9ad-41f3-8e9a-618d40c82e3a][field1]" in names
    assert "org" in names


async def test_read_form_rejects_unsupported_ats(page: Page) -> None:
    with pytest.raises(UnsupportedForm):
        await read_form(page, Ats.softgarden)


@respx.mock
async def test_job_fields_greenhouse_uses_questions_api() -> None:
    route = respx.get(GITLAB_QUESTIONS, params={"questions": "true"}).mock(
        return_value=httpx.Response(200, json=greenhouse_payload())
    )

    fields = await job_fields(make_job(Ats.greenhouse))

    assert route.called
    assert "question_37369962002" in by_name(fields)


async def test_job_fields_external_job_is_unsupported() -> None:
    with pytest.raises(UnsupportedForm, match="manual"):
        await job_fields(make_job(Ats.external, slug="levels.fyi/acme"))
