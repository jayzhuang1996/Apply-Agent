"""
Shared Moonshot (Kimi) client setup.
Uses the OpenAI-compatible endpoint at https://api.moonshot.ai/v1
"""

import os
from openai import OpenAI

MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"

MODEL_LARGE = "moonshot-v1-auto"    # smart, handles complex tasks
MODEL_FAST  = "moonshot-v1-auto"    # same model — moonshot auto-routes by context length


def get_client() -> OpenAI:
    api_key = os.getenv("MOONSHOT_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "MOONSHOT_API_KEY not set. Add it to your .env file or run:\n"
            "  export MOONSHOT_API_KEY=your-key-here"
        )
    return OpenAI(base_url=MOONSHOT_BASE_URL, api_key=api_key)


def chat(
    system: str,
    user: str,
    model: str = MODEL_LARGE,
    max_tokens: int = 4096,
    client: OpenAI | None = None,
) -> str:
    """Single-turn chat. Returns the response text."""
    if client is None:
        client = get_client()

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content.strip()
