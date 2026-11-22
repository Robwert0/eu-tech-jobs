"""Specs for jobhunt.ats.text.html_to_text: job descriptions as plain text."""

from jobhunt.ats.text import html_to_text


def test_tags_removed_and_blocks_separated() -> None:
    assert html_to_text("<p>Hello</p><p>World</p>") == "Hello World"


def test_entities_decoded() -> None:
    assert html_to_text("<p>R&amp;D <b>team</b> &gt; 5</p>") == "R&D team > 5"


def test_whitespace_collapsed() -> None:
    assert html_to_text("<ul>\n  <li>Python</li>\n  <li>SQL</li>\n</ul>") == "Python SQL"


def test_empty_input() -> None:
    assert html_to_text("") == ""
