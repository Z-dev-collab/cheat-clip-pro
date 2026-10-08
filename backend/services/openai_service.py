"""
OpenAI-compatible provider support for Cheat Clip Pro.

Lets the app run its clip analysis through ANY OpenAI-compatible endpoint
instead of (or in addition to) Google Gemini. The primary target is a local
9router instance (http://localhost:20128/v1), but any compatible server works:
OpenRouter, LM Studio, Ollama's OpenAI bridge, vLLM, LiteLLM, etc.

The public surface is deliberately tiny:
    normalize_openai_base_url(base_url)          -> str
    openai_list_models(base_url, api_key)        -> List[str]
    openai_generate_json(base_url, api_key, ...) -> dict
"""

import json
import re
from typing import Any, Dict, List, Optional

import requests

from backend.config import logger

# Default endpoint used when the user picks the "9router" provider and leaves
# the base URL blank. 9router exposes an OpenAI-compatible API on this port.
DEFAULT_9ROUTER_BASE_URL = "http://localhost:20128/v1"

# Models that are obviously not text-chat models; hidden from the picker so the
# 9router catalogue (hundreds of entries) stays usable.
_NON_CHAT_HINTS = (
    "embed", "embedding", "whisper", "tts", "image", "dall-e", "dalle",
    "moderation", "rerank", "lyria", "parakeet", "asr", "video",
)

_SYSTEM_PROMPT = (
    "You are a JSON-only analysis API. "
    "You MUST reply with ONE single valid JSON object and absolutely nothing else — "
    "no markdown fences, no commentary, no explanations before or after."
)


def normalize_openai_base_url(base_url: str) -> str:
    """Normalizes a user supplied base URL into an OpenAI v1 root.

    'http://localhost:20128'      -> 'http://localhost:20128/v1'
    'http://localhost:20128/v1'   -> 'http://localhost:20128/v1'
    'http://localhost:20128/v1/'  -> 'http://localhost:20128/v1'
    '' (empty)                    -> the default 9router URL
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_9ROUTER_BASE_URL
    # Append /v1 when the user only gave scheme+host (+ optional path).
    if not re.search(r"/v\d+[a-z0-9]*$", base, re.IGNORECASE):
        base = f"{base}/v1"
    return base


def _headers(api_key: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def is_chat_model(model_id: str) -> bool:
    """Heuristic filter: keep plausible chat models, drop embeddings/audio/image."""
    low = (model_id or "").lower()
    if not low:
        return False
    return not any(hint in low for hint in _NON_CHAT_HINTS)


def openai_list_models(base_url: str, api_key: str = "", timeout: int = 20) -> List[str]:
    """GET {base}/models and return the list of model ids (chat models first)."""
    url = f"{normalize_openai_base_url(base_url)}/models"
    try:
        resp = requests.get(url, headers=_headers(api_key), timeout=timeout)
        if resp.status_code != 200:
            logger.warning(
                f"OpenAI-compatible model listing failed at {url}: HTTP {resp.status_code} {resp.text[:160]}"
            )
            return []
        payload = resp.json()
        raw_items = payload.get("data") or payload.get("models") or []
        chat_models: List[str] = []
        other_models: List[str] = []
        for item in raw_items:
            if isinstance(item, str):
                model_id = item
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or ""
            else:
                model_id = ""
            model_id = (model_id or "").strip()
            if not model_id:
                continue
            target = chat_models if is_chat_model(model_id) else other_models
            if model_id not in target and model_id not in chat_models:
                target.append(model_id)
        # De-dupe while preserving the catalogue order inside each bucket.
        ordered = list(dict.fromkeys(chat_models)) + [
            m for m in dict.fromkeys(other_models) if m not in chat_models
        ]
        logger.info(f"OpenAI-compatible provider at {url} exposes {len(ordered)} models.")
        return ordered
    except Exception as exc:  # noqa: BLE001 - never break the endpoint on a probe
        logger.warning(f"OpenAI-compatible model listing error at {url}: {exc}")
        return []


def _extract_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from a model reply."""
    if not text:
        return None
    cleaned = text.strip()
    # Strip markdown fences.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    # Fall back to the outermost {...} block.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def openai_generate_json(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    temperature: float = 0.2,
    timeout: int = 420,
) -> dict:
    """Runs a chat completion and returns the parsed JSON object.

    Raises RuntimeError with a human readable message on failure so the router
    can surface a helpful SSE error.
    """
    url = f"{normalize_openai_base_url(base_url)}/chat/completions"

    user_prompt = prompt
    if schema:
        user_prompt = (
            f"{prompt}\n\n"
            "Respond with a SINGLE JSON object that strictly matches this JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
            "Do not wrap the JSON in markdown code fences."
        )

    base_payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }

    # Try structured JSON mode first, then degrade gracefully for servers that
    # do not implement response_format.
    attempts = [
        {**base_payload, "response_format": {"type": "json_object"}},
        base_payload,
    ]

    last_error: Optional[str] = None
    for payload in attempts:
        try:
            resp = requests.post(url, headers=_headers(api_key), json=payload, timeout=timeout)
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Provider timed out after {timeout}s while generating with '{model}'. "
                "The model may be overloaded — try a different model or retry."
            )
        except Exception as exc:  # noqa: BLE001
            last_error = f"Network error contacting {url}: {exc}"
            logger.warning(last_error)
            continue

        if resp.status_code != 200:
            snippet = (resp.text or "")[:300]
            # If response_format was the problem, retry without it.
            if "response_format" in payload and resp.status_code in (400, 422):
                last_error = f"HTTP {resp.status_code}: {snippet}"
                continue
            raise RuntimeError(
                f"Provider '{model}' returned HTTP {resp.status_code}: {snippet}"
            )

        try:
            data = resp.json()
        except Exception:
            last_error = "Provider returned a non-JSON HTTP body."
            continue

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"Provider '{model}' error: {msg}")

        choices = (data or {}).get("choices") or []
        if not choices:
            last_error = "Provider returned no choices."
            continue

        message = choices[0].get("message") or {}
        content = message.get("content")
        # Some servers return content as a list of parts.
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )

        parsed = _extract_json_object(content or "")
        if parsed is None:
            last_error = f"Model '{model}' did not return parseable JSON."
            logger.warning(f"{last_error} Raw head: {(content or '')[:200]}")
            continue
        return parsed

    raise RuntimeError(last_error or "OpenAI-compatible generation failed for unknown reasons.")
