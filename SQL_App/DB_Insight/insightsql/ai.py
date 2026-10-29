from __future__ import annotations

import re

import httpx

from insightsql.config import Settings


class AIUnavailable(Exception):
    pass


class AIGenerationFailed(Exception):
    pass


async def generate_sql(question: str, schema: str, settings: Settings) -> str:
    if not settings.openai_api_key:
        raise AIUnavailable("AI generation is not configured. Add OPENAI_API_KEY to your .env file.")

    system_prompt = (
        "Generate exactly one SQLite SELECT statement for the user's read-only analytics question. "
        "Use only the supplied tables and columns. Never include writes, schema changes, PRAGMA, "
        "ATTACH, extension functions, markdown, or explanatory text. Do not select sensitive columns "
        "unless the question explicitly requires them. Use a reasonable LIMIT when returning rows."
    )
    payload = {
        "model": settings.openai_model,
        "temperature": 0,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Approved schema:\n{schema}\n\nQuestion:\n{question}"},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            response = await client.post(
                settings.openai_chat_completions_url,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise AIGenerationFailed("The AI service could not be reached. Try again shortly.") from exc
    except (httpx.HTTPStatusError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIGenerationFailed("The AI service returned an invalid response. Try again or edit SQL manually.") from exc

    if not isinstance(content, str) or not content.strip():
        raise AIGenerationFailed("The AI service returned no SQL. Try rephrasing the question.")
    match = re.fullmatch(r"\s*```(?:sql)?\s*(.*?)\s*```\s*", content, flags=re.IGNORECASE | re.DOTALL)
    return (match.group(1) if match else content).strip()