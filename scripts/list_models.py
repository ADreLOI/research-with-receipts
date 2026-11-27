"""Print the models available on Vultr Serverless Inference.

Usage: python scripts/list_models.py   (reads VULTR_INFERENCE_API_KEY from env or .env)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm import VultrInference  # noqa: E402


async def main() -> None:
    if not settings.inference_api_key:
        sys.exit("VULTR_INFERENCE_API_KEY is not set (see .env.example)")
    client = VultrInference()
    for model in await client.list_models():
        print(model)
    text, vision = await client.resolve_models()
    print(f"\nauto-picked text model:   {text}\nauto-picked vision model: {vision or '(none)'}")


asyncio.run(main())
