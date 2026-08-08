"""Shared helpers for the consistency engine v3.

Only code that must be *identical* across the write path, the reconciliation
path and the check path lives here — so no implementation drifts:

  * ``hash_content``    — the single content-hash implementation (sha256[:40]).
    Used by events, periodic audit and check-time hash reconciliation.
  * ``parse_json``      — the single LLM-JSON extraction helper.
  * ``llm_json``        — the single time-boxed streaming LLM-to-JSON call.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Callable

from app.llm.client import LLMClient
from app.llm.types import Message


def hash_content(content: str) -> str:
    """One implementation of the content hash: ``sha256(content)[:40]``.

    Used by ORM events, the periodic audit and check-time reconciliation so
    the three layers can never drift apart (v3 design §14.2).
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:40]


def parse_json(content: str) -> dict | None:
    """Three-level JSON extraction: raw → fenced → balanced-brace scan."""
    if not content:
        return None
    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(content)
    start = content.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(content[start : i + 1])
                    break
    for cand in candidates:
        if not cand.strip():
            continue
        try:
            data = json.loads(cand.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK ~1 token/char, so len//2 is conservative."""
    if not text:
        return 0
    return max(1, len(text) // 2)


async def llm_json(
    client: LLMClient,
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    reasoning_effort: str,
    temperature: float,
    timeout: float = 90.0,
    on_failure: Callable[[str], None] | None = None,
) -> dict | None:
    """One streaming, time-boxed, JSON-returning LLM call.

    ``max_tokens=None`` leaves the API's default ceiling in place — callers
    that don't need to constrain output length simply omit it. Failures and
    timeouts never raise: they return ``None`` and notify *on_failure* so the
    caller can tell a degraded report apart from a clean one.
    """
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user),
    ]
    parts: list[str] = []

    async def _collect() -> None:
        kwargs: dict = {
            "messages": messages,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        async for tok in client.chat_stream_tokens(**kwargs):
            parts.append(tok)

    try:
        await asyncio.wait_for(_collect(), timeout=timeout)
    except asyncio.TimeoutError:
        if on_failure:
            on_failure(f"LLM call timed out after {timeout:.0f}s")
        return None
    except Exception as exc:
        if on_failure:
            on_failure(str(exc))
        return None
    return parse_json("".join(parts))