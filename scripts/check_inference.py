"""Check the Vultr Serverless Inference setup end to end.

Usage: python scripts/check_inference.py      (reads .env)
Prints the available models, what gets auto-picked, and makes one tiny
chat call so you know the key, base URL and model all work.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm import LLMError, extract_json  # noqa: E402
from app.providers import VultrProvider  # noqa: E402


async def main() -> int:
    print(f"base url : {settings.inference_base_url}")
    if not settings.inference_api_key:
        print("api key  : MISSING. Set VULTR_INFERENCE_API_KEY in .env (Vultr portal > Serverless > Inference).")
        print("           Without it the app runs the labelled mock policy.")
        return 1
    print("api key  : set (not shown)")
    p = VultrProvider()
    ok, detail = await p.ping()
    print(f"reachable: {ok} ({detail})")
    if not ok:
        return 1
    try:
        models = await p.list_models()
    except LLMError as exc:
        print(f"models   : {exc}")
        return 1
    print(f"models   : {len(models)} available")
    for m in models:
        print(f"  - {m}")
    model, vision = await p.resolve_models()
    print(f"text     : {model}{' (from LLM_MODEL)' if settings.llm_model else ' (auto-picked)'}")
    print(f"vision   : {vision or '(none)'}")
    print(f"fallback : {settings.llm_fallback_model or '(none)'}")
    t0 = time.monotonic()
    try:
        raw = await p.chat([{"role": "user", "content": 'Reply with exactly this JSON and nothing else: {"ok": true}'}],
                           max_tokens=50, temperature=0)
        parsed = extract_json(raw)
        print(f"chat     : OK in {time.monotonic() - t0:.1f}s via {p.active_model}: {parsed}")
    except (LLMError, ValueError) as exc:
        print(f"chat     : FAILED: {exc}")
        return 1
    return 0


sys.exit(asyncio.run(main()))
