import asyncio
import json

import httpx
import pytest

import app.agent as agent_mod
from app.agent import execute_run
from app.llm import LLMError
from app.providers import MockProvider, VultrProvider
from app.runs import registry


def _reply(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _provider(handler) -> VultrProvider:
    p = VultrProvider(transport=httpx.MockTransport(handler))
    p.key, p.model, p.fallback_model, p._vision_disabled = "test-key", "primary-model", "", True
    VultrProvider._ping_cache = None
    return p


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    async def no_sleep(_s):
        return None
    monkeypatch.setattr("app.providers.vultr.asyncio.sleep", no_sleep)


def test_retries_transient_errors_then_succeeds():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=_reply('{"ok": true}'))

    p = _provider(handler)
    assert asyncio.run(p.chat([{"role": "user", "content": "hi"}])) == '{"ok": true}'
    assert len(calls) == 3
    assert calls[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(calls[0].content)["model"] == "primary-model"


def test_gives_up_with_clear_error():
    p = _provider(lambda req: httpx.Response(503, text="down"))
    with pytest.raises(LLMError, match="unavailable after 3 attempts"):
        asyncio.run(p.chat([{"role": "user", "content": "hi"}]))


def test_bad_key_is_reported_without_retrying():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(401, text="nope")

    with pytest.raises(LLMError, match="VULTR_INFERENCE_API_KEY"):
        asyncio.run(_provider(handler).chat([{"role": "user", "content": "hi"}]))
    assert len(calls) == 1


def test_falls_back_to_second_model_and_sticks_with_it():
    seen = []

    def handler(req):
        model = json.loads(req.content)["model"]
        seen.append(model)
        if model == "primary-model":
            return httpx.Response(404, text='{"error": "model not found"}')
        return httpx.Response(200, json=_reply("from fallback"))

    p = _provider(handler)
    p.fallback_model = "backup-model"
    assert asyncio.run(p.chat([{"role": "user", "content": "a"}])) == "from fallback"
    assert asyncio.run(p.chat([{"role": "user", "content": "b"}])) == "from fallback"
    assert seen == ["primary-model", "backup-model", "backup-model"]


def test_strips_reasoning_blocks():
    p = _provider(lambda req: httpx.Response(200, json=_reply("<think>plan</think>{\"a\": 1}")))
    assert asyncio.run(p.chat([{"role": "user", "content": "x"}])) == '{"a": 1}'


def test_auto_picks_models_from_list():
    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "some-embedder"}, {"id": "Qwen2.5-VL-7B"},
                                                      {"id": "llama-3.3-70b-instruct"}]})
        return httpx.Response(200, json=_reply("ok"))

    p = _provider(handler)
    p.model, p._vision_disabled = "", False
    assert asyncio.run(p.resolve_models()) == ("llama-3.3-70b-instruct", "Qwen2.5-VL-7B")


def test_auto_pick_skips_embedding_and_guard_models():
    def handler(req):
        return httpx.Response(200, json={"data": [{"id": "llama-guard-3-8b"}, {"id": "qwen3-embedding-8b"},
                                                  {"id": "bge-m3"}, {"id": "mistral-small-3.1-24b-instruct"}]})

    p = _provider(handler)
    p.model = ""
    assert asyncio.run(p.resolve_models())[0] == "mistral-small-3.1-24b-instruct"


def test_reasoning_only_reply_is_used_and_truncation_is_explained():
    replies = iter([
        {"choices": [{"message": {"content": "", "reasoning_content": 'hmm... {"thought": "t", "action": "final"}'}}]},
        {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    ])
    p = _provider(lambda req: httpx.Response(200, json=next(replies)))
    assert asyncio.run(p.chat([{"role": "user", "content": "x"}])) == '{"thought": "t", "action": "final"}'
    with pytest.raises(LLMError, match="LLM_MAX_TOKENS"):
        asyncio.run(p.chat([{"role": "user", "content": "x"}]))


def test_unreachable_vultr_falls_back_to_labelled_mock(monkeypatch):
    monkeypatch.setattr(agent_mod, "make_live_provider",
                        lambda: _provider(lambda req: httpx.Response(503, text="down")))
    run = registry.create("How does gVisor isolate containers compared to runc?")
    asyncio.run(execute_run(run))
    assert run.status == "done", run.error
    assert any(e["type"] == "fallback" for e in run.events)
    assert run.llm.startswith("mock policy (fallback")


def test_mock_provider_returns_valid_actions():
    out = asyncio.run(MockProvider().chat([{"role": "system", "content": "s"},
                                           {"role": "user", "content": "Question: what is gVisor?"}]))
    assert json.loads(out)["action"] == "search"


def test_context_overflow_is_not_mistaken_for_a_missing_model():
    from app.llm import ContextTooLong

    calls = []

    def handler(req):
        calls.append(json.loads(req.content)["model"])
        return httpx.Response(400, json={"error": "This model's maximum context length is 8192 tokens."})

    p = _provider(handler)
    p.fallback_model = "backup-model"
    with pytest.raises(ContextTooLong):
        asyncio.run(p.chat([{"role": "user", "content": "x"}]))
    assert calls == ["primary-model"]  # no pointless fallback: a shorter prompt is the fix


def test_auto_pick_matches_the_models_that_worked_on_vultr():
    listing = ["qwen3-asr-1.7b", "wan2.2-t2v-video", "router-decision-1b", "mimo-v2.6-flash", "qwen3.8-27b",
               "deepseek-v4.1-flash-1017", "qwen3-tts-flash"]

    p = _provider(lambda req: httpx.Response(200, json={"data": [{"id": m} for m in listing]}))
    p.model, p.fallback_model, p._vision_disabled = "", "", False
    assert asyncio.run(p.resolve_models()) == ("deepseek-v4.1-flash-1017", "mimo-v2.6-flash")
    assert p.fallback_model == "qwen3.8-27b"
