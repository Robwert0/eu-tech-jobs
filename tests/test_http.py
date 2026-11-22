import httpx
import pytest
import respx

from jobhunt.http import FetchError, PoliteClient


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client(t: FakeTime, interval: float = 0.0) -> PoliteClient:
    return PoliteClient(min_interval=interval, clock=t.clock, sleep=t.sleep)


@respx.mock
async def test_retries_429_then_succeeds() -> None:
    route = respx.get("https://a.test/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(429), httpx.Response(200, json={"ok": 1})]
    )
    t = FakeTime()
    async with client(t) as c:
        assert await c.get_json("https://a.test/x") == {"ok": 1}
    assert route.call_count == 3
    assert t.sleeps == [0.5, 1.0]


@respx.mock
async def test_gives_up_after_three_retries() -> None:
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(503))
    async with client(FakeTime()) as c:
        with pytest.raises(FetchError) as e:
            await c.get_json("https://a.test/x")
    assert e.value.status == 503
    assert route.call_count == 4


@respx.mock
async def test_timeout_is_retried_then_fails_without_status() -> None:
    route = respx.get("https://a.test/x").mock(side_effect=httpx.ReadTimeout("slow"))
    async with client(FakeTime()) as c:
        with pytest.raises(FetchError) as e:
            await c.get_text("https://a.test/x")
    assert e.value.status is None
    assert route.call_count == 4


@respx.mock
async def test_404_is_not_retried() -> None:
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(404))
    async with client(FakeTime()) as c:
        with pytest.raises(FetchError) as e:
            await c.get_json("https://a.test/x")
    assert e.value.status == 404
    assert route.call_count == 1


@respx.mock
async def test_same_host_requests_are_spaced() -> None:
    t = FakeTime()
    starts: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        starts.append(t.now)
        return httpx.Response(200, text="hi")

    respx.get(url__startswith="https://a.test/").mock(side_effect=record)
    async with client(t, interval=0.2) as c:
        await c.get_text("https://a.test/1")
        await c.get_text("https://a.test/2")
    assert starts[1] - starts[0] >= 0.2


@respx.mock
async def test_post_json_sends_body_and_user_agent() -> None:
    route = respx.post("https://a.test/p").mock(return_value=httpx.Response(200, json=[1]))
    async with client(FakeTime()) as c:
        assert await c.post_json("https://a.test/p", {"a": 1}, headers={"X-K": "v"}) == [1]
    req = route.calls[0].request
    assert req.headers["User-Agent"] == "jobhunt/0.1"
    assert req.headers["X-K"] == "v"
    assert req.content == b'{"a":1}'


@respx.mock
async def test_get_page_returns_final_url_after_redirect() -> None:
    respx.get("https://a.test/careers").mock(
        return_value=httpx.Response(301, headers={"Location": "https://b.test/jobs"})
    )
    respx.get("https://b.test/jobs").mock(return_value=httpx.Response(200, text="hello"))
    async with client(FakeTime()) as c:
        assert await c.get_page("https://a.test/careers") == ("https://b.test/jobs", "hello")
