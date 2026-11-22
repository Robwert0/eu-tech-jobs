from html.parser import HTMLParser


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pieces: list[str] = []

    def handle_data(self, data: str) -> None:
        self.pieces.append(data)


def html_to_text(html: str) -> str:
    parser = _TextCollector()
    parser.feed(html)
    parser.close()
    text = " ".join(parser.pieces)
    return " ".join(text.split())
