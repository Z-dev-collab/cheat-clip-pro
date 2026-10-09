"""Per-part caption recommendations for the film (Archive.org) workflow.

When a movie is split into consecutive parts, each part is uploaded/scheduled
as its own post, so each part needs its own engaging caption + hashtags. This
service asks the user's configured LLM (Gemini or any OpenAI-compatible
endpoint, e.g. 9router) for those captions in ONE batched call, and falls back
to a deterministic template when no model/keys are available so the film flow
never hard-fails.

Public surface:
    generate_part_captions(title, parts, ...) -> List[{"index", "caption", "hashtags"}]
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from backend.config import logger

# Keep the prompt bounded — a feature-length film can be dozens of parts.
_MAX_PARTS_PER_CALL = 120


def _fallback_caption(title: str, label: str) -> Dict[str, str]:
    base = (title or "Film").strip()
    label = (label or "").strip()
    hashtags = "#film #movie #viral #trending"
    head = f"{base} — {label}".strip(" —") if label else base
    return {"caption": f"{head} 🎬 {hashtags}", "hashtags": hashtags}


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:  # noqa: BLE001
            return None
    return None


def _build_prompt(title: str, parts: List[Dict], language_name: str) -> str:
    lang = (language_name or "").strip() or "the film's original language"
    indexed = [
        {
            "i": int(p.get("index", i)),
            "part": (p.get("label") or f"Part {i + 1}").strip(),
        }
        for i, p in enumerate(parts)
    ]
    return (
        "You are a social-media copywriter for serialized movie uploads.\n"
        f"The film is titled: \"{title}\".\n"
        "For EACH part below write ONE short, engaging caption (max ~200 characters) "
        f"plus 3-5 relevant hashtags. Write everything in {lang}.\n"
        "RULES:\n"
        "1. Return the SAME number of entries as the input, keeping the SAME `i` index.\n"
        "2. The caption must tease that part's content without spoiling the ending.\n"
        "3. Never merge, split, reorder or drop entries.\n"
        "4. Return a JSON object: {\"parts\": [{\"i\": 0, \"caption\": \"...\", \"hashtags\": \"#a #b\"}, ...]}\n\n"
        f"INPUT PARTS (JSON):\n{json.dumps(indexed, ensure_ascii=False)}"
    )


def _call_openai(
    prompt: str, base_url: str, api_key: str, model: str
) -> Optional[dict]:
    from backend.services.openai_service import (
        openai_generate_json,
        openai_list_models,
    )

    catalogue = openai_list_models(base_url, api_key)
    models_to_try: List[str] = []
    if model and not model.lower().startswith("gemini-"):
        models_to_try.append(model)
    for needle in ("hermesagent", "gpt-", "claude", "gemini/", "auto"):
        for candidate in catalogue:
            if needle in candidate.lower() and candidate not in models_to_try:
                models_to_try.append(candidate)
    for candidate in catalogue:
        if candidate not in models_to_try:
            models_to_try.append(candidate)
    if not models_to_try:
        models_to_try = [model or "hermesagent"]

    last_err: Optional[Exception] = None
    for model_name in models_to_try[:8]:
        try:
            return openai_generate_json(
                base_url, api_key, model_name, prompt,
                {"type": "object", "properties": {"parts": {"type": "array"}}},
                0.7, 420,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning(f"Caption attempt with {model_name} failed: {exc}")
    if last_err:
        logger.warning(f"Caption generation (OpenAI path) failed: {last_err}")
    return None


def _call_gemini(prompt: str, api_key: str, model: str) -> Optional[dict]:
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Gemini SDK unavailable for captions: {exc}")
        return None

    from backend.services.ai_service import (
        KNOWN_FLASH_MODELS,
        get_flash_models_for_key,
    )

    client = genai.Client(api_key=api_key)
    discovered = get_flash_models_for_key(client)
    models_to_try: List[str] = [model] if model else []
    for fm in list(discovered) + list(KNOWN_FLASH_MODELS):
        if fm not in models_to_try:
            models_to_try.append(fm)

    last_err: Optional[Exception] = None
    for model_name in models_to_try:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.7,
                    max_output_tokens=65536
                    if any(v in model_name for v in ["2.0", "2.5", "3."])
                    else 8192,
                ),
            )
            raw_text = (getattr(resp, "text", None) or "").strip()
            parsed = _extract_json(raw_text)
            if parsed is not None:
                return parsed
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning(f"Caption attempt with {model_name} failed: {exc}")
    if last_err:
        logger.warning(f"Caption generation (Gemini path) failed: {last_err}")
    return None


def generate_part_captions(
    title: str,
    parts: List[Dict],
    language_name: str = "",
    provider: str = "gemini",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
) -> List[Dict[str, str]]:
    """Return one {index, caption, hashtags} entry per part.

    Always returns a full list (LLM output when available, deterministic
    template otherwise) so the caller can attach a caption to every part.
    """
    safe_parts = list(parts or [])[:_MAX_PARTS_PER_CALL]
    fallback = [
        {
            "index": int(p.get("index", i)),
            "caption": _fallback_caption(title, p.get("label") or f"Part {i + 1}")["caption"],
            "hashtags": _fallback_caption(title, p.get("label") or f"Part {i + 1}")["hashtags"],
        }
        for i, p in enumerate(safe_parts)
    ]
    if not safe_parts:
        return []

    prompt = _build_prompt(title, safe_parts, language_name)
    is_openai = provider in ("openai", "9router", "openai-compatible", "local")

    parsed: Optional[dict] = None
    try:
        if is_openai:
            parsed = _call_openai(prompt, base_url, api_key, model)
        elif api_key:
            parsed = _call_gemini(prompt, api_key, model)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Caption generation failed, using fallback: {exc}")
        parsed = None

    if not isinstance(parsed, dict):
        return fallback

    by_index: Dict[int, Dict[str, str]] = {}
    for item in parsed.get("parts") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        caption = str(item.get("caption") or "").strip()
        hashtags = str(item.get("hashtags") or "").strip()
        if caption:
            by_index[idx] = {"caption": caption, "hashtags": hashtags}

    result: List[Dict[str, str]] = []
    for i, p in enumerate(safe_parts):
        idx = int(p.get("index", i))
        got = by_index.get(idx)
        if got:
            result.append({"index": idx, "caption": got["caption"], "hashtags": got["hashtags"]})
        else:
            fb = fallback[i]
            result.append(fb)
    return result
