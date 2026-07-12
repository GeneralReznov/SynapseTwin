"""Self-contained AI helper for the Render Workflow service.

Sarvam AI is primary for all reasoning; Groq is the automatic fallback if
Sarvam is unavailable or fails. This mirrors the main SynapseTwin app's
provider order but is kept independent so this folder can be pushed to its
own repo and deployed as its own Render service with no dependency on the
rest of the codebase.
"""
from __future__ import annotations
import os
import re
import json
import asyncio
import logging

import httpx

logger = logging.getLogger("synapsetwin.workflow.ai")

SARVAM_KEY      = os.getenv("SARVAM_API_KEY", "")
SARVAM_LLM_BASE = "https://api.sarvam.ai/v1"

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL    = "llama-3.3-70b-versatile"


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def _sarvam_chat(system_prompt: str, user_message: str, json_mode: bool) -> dict:
    if not SARVAM_KEY:
        return {"success": False, "content": "", "error": "SARVAM_API_KEY not configured"}
    payload = {
        "model": "sarvam-30b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            f"{SARVAM_LLM_BASE}/chat/completions",
            json=payload,
            headers={"api-subscription-key": SARVAM_KEY, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        if json_mode:
            content = _strip_fences(content)
        return {"success": True, "content": content, "provider": "sarvam"}


async def _groq_chat(system_prompt: str, user_message: str, json_mode: bool) -> dict:
    if not GROQ_API_KEY:
        return {"success": False, "content": "", "error": "GROQ_API_KEY not configured"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            f"{GROQ_BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        if json_mode:
            content = _strip_fences(content)
        return {"success": True, "content": content, "provider": "groq"}


async def chat_completion_async(system_prompt: str, user_message: str, json_mode: bool = False) -> dict:
    """Sarvam AI first (with retries), Groq as the fallback."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            result = await _sarvam_chat(system_prompt, user_message, json_mode)
            if result["success"]:
                return result
            last_exc = RuntimeError(result.get("error", "unknown Sarvam error"))
        except Exception as exc:
            last_exc = exc
        if attempt < 2:
            await asyncio.sleep(1.5 * (attempt + 1))

    logger.warning(f"Sarvam failed ({last_exc}) — falling back to Groq")
    try:
        result = await _groq_chat(system_prompt, user_message, json_mode)
        if result["success"]:
            return result
    except Exception as exc:
        logger.error(f"Groq fallback also failed: {exc}")

    return {"success": False, "content": "", "provider": "none"}


def chat_completion(system_prompt: str, user_message: str, json_mode: bool = False) -> dict:
    """Sync wrapper — Render task functions are plain sync/async Python functions."""
    return asyncio.run(chat_completion_async(system_prompt, user_message, json_mode))


def analyze_json(system_prompt: str, user_message: str) -> dict:
    result = chat_completion(system_prompt, user_message, json_mode=True)
    if not result.get("success"):
        return {}
    try:
        return json.loads(result["content"])
    except Exception as e:
        logger.error(f"JSON parse error: {e} — content: {result.get('content', '')[:200]}")
        return {}
