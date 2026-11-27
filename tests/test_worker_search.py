"""The in-sandbox search falls back engine by engine (no network needed: httpx is faked)."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parent.parent / "sandbox_image" / "worker.py"

LITE_HTML = """<table>
<tr><td><a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpop">World population</a></td></tr>
<tr><td class="result-snippet">India, China and the US hold about 40%.</td></tr>
</table>"""


class FakeResponse:
    def __init__(self, text="", data=None):
        self.text, self._data = text, data

    def json(self):
        return self._data


def load_worker(post, get):
    spec = importlib.util.spec_from_file_location("rwr_worker_under_test", WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = types.SimpleNamespace(post=post, get=get)
    return mod, fake


@pytest.fixture
def run_search(monkeypatch):
    def _run(post, get, query="What share of the world population do the three biggest countries hold?"):
        mod, fake = load_worker(post, get)
        monkeypatch.setitem(sys.modules, "httpx", fake)
        return mod.search({"query": query, "max_results": 5})
    return _run


def test_lite_endpoint_used_when_html_endpoint_is_empty(run_search):
    def post(url, **kw):
        return FakeResponse(LITE_HTML if "lite" in url else "<html>anomaly</html>")

    out = run_search(post, lambda *a, **k: pytest.fail("wikipedia should not be needed"))
    assert out["provider"].startswith("duckduckgo-lite")
    assert out["results"][0]["url"] == "https://example.org/pop"
    assert "40%" in out["results"][0]["snippet"]


def test_wikipedia_keyword_retry_when_everything_else_is_empty(run_search):
    seen = []

    def get(url, params=None, **kw):
        seen.append(params["srsearch"])
        hits = [{"title": "World population", "snippet": "<b>world</b> population"}] if len(seen) > 1 else []
        return FakeResponse(data={"query": {"search": hits}})

    out = run_search(lambda url, **kw: FakeResponse("<html></html>"), get)
    assert out["provider"].startswith("wikipedia (keywords)")
    assert seen[1] == "share world population three biggest countries"
    assert out["results"][0]["url"] == "https://en.wikipedia.org/wiki/World_population"


def test_all_engines_failing_returns_empty_with_reasons(run_search):
    def boom(*a, **k):
        raise OSError("no route")

    out = run_search(boom, boom)
    assert out["results"] == [] and "duckduckgo: OSError" in out["provider"]
