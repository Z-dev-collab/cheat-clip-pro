import asyncio
import json
import logging
import os
import re
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types
from pathlib import Path

from backend.config import (
    TEMP_DIR,
    UPLOADS_DIR,
    capture_youtube_live_media,
    compute_audio_energy_heatmap,
    get_video_file_metadata,
    logger,
    transcribe_local_video_file,
)
from backend.schemas.analyze import (
    AnalyzeRequest,
    AnalyzeResponse,
    HeatmapPoint,
    TranscriptLine,
    TranslateRequest,
    TranslateResponse,
    VideoAnalysis,
    ViralClip,
)
from backend.services.ai_service import (
    KNOWN_FLASH_MODELS,
    get_flash_models_for_key,
    list_available_gemini_models,
)
from backend.services.openai_service import (
    DEFAULT_9ROUTER_BASE_URL,
    normalize_openai_base_url,
    openai_generate_json,
    openai_list_models,
)
from backend.services.gdrive_service import (
    download_google_drive_video,
    is_google_drive_url,
)
from backend.services.generic_video_service import (
    download_generic_video,
    fetch_generic_metadata,
    is_generic_video_url,
)
from backend.services.youtube_service import (
    fetch_transcript,
    fetch_video_metadata,
    get_supadata_keys,
    get_supadata_usage_data,
)
from backend.utils.heatmap import get_average_heatmap_value
from backend.utils.languages import SUPPORTED_LANGUAGES, resolve_language_name
from backend.utils.proxy import get_proxy_url
from backend.utils.sse import _sse
from backend.utils.text import (
    detect_transcript_language,
    extract_video_id,
    lowercase_hashtags_in_string,
    parse_manual_subtitles,
    sanitize_first_person_title,
)

router = APIRouter(tags=["Analyze"])

# How much of a live / recently-ended broadcast to capture locally for the
# offline Whisper fallback (seconds). 1800s (30 min) keeps transcription quick
# while still covering a typical highlight window; the YouTube DVR window is
# 60 min, so this always fits.
LIVE_FALLBACK_SECONDS = 1800


@router.get("/api/health")
def health_check(refresh: bool = False):
    is_vercel = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    keys = get_supadata_keys()
    proxy = get_proxy_url()
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    supadata_info = get_supadata_usage_data(force=refresh) if keys else {
        "total_keys": 0,
        "total_limit": 0,
        "total_used": 0,
        "total_remaining": 0,
        "usage_percent": 0.0,
        "active_keys": 0,
        "exhausted_keys": 0,
        "keys_detail": [],
        "status": "not_configured"
    }
    return {
        "status": "ok",
        "message": "CHEAT CLIP PRO API is active",
        "is_vercel": is_vercel,
        "proxy_configured": bool(proxy),
        "gemini_env_configured": has_gemini,
        "supadata_keys_count": len(keys),
        "supadata": supadata_info
    }


@router.get("/api/supadata-usage")
def supadata_usage_endpoint(refresh: bool = False):
    """Returns real-time usage and remaining credit quota across all configured Supadata API keys."""
    return get_supadata_usage_data(force=refresh)


@router.get("/api/models")
def list_available_models(api_key: str = "", provider: str = "gemini", base_url: str = ""):
    """Lists models for the selected provider.

    - provider=gemini  → Google Gemini models (Flash first, newest first).
    - provider=openai  → any OpenAI-compatible endpoint (e.g. 9router) model catalogue.
    """
    if (provider or "gemini").strip().lower() in ("openai", "9router", "openai-compatible", "local"):
        models = openai_list_models(base_url or DEFAULT_9ROUTER_BASE_URL, api_key)
        return {"models": models, "provider": "openai"}
    models = list_available_gemini_models(api_key)
    return {"models": models, "provider": "gemini"}


@router.get("/api/languages")
def list_languages():
    """Returns every language the UI can offer for transcript & title translation.

    Each entry: {"code": "id", "name": "Indonesian (Bahasa Indonesia)"}.
    """
    languages = [{"code": code, "name": name} for code, name in SUPPORTED_LANGUAGES.items()]
    return {"languages": languages, "count": len(languages)}


def _translate_lines_sync(
    indexed_lines: List[dict],
    language_name: str,
    source_language: Optional[str],
    provider: str,
    base_url: str,
    api_key: str,
    requested_model: str,
) -> dict:
    """Synchronous transcript translation via Gemini or any OpenAI-compatible endpoint.

    Returns the raw parsed JSON dict (expected shape: {"lines": [{"i": int, "text": str}]}).
    Shared by the /api/translate endpoint and the analyze flow.
    """
    is_openai_provider = provider in ("openai", "9router", "openai-compatible", "local")
    source_hint = resolve_language_name(source_language) if source_language else ""

    prompt = (
        f"You are a professional subtitle translator. Translate the following transcript into "
        f"{language_name}.\n\n"
        f"RULES:\n"
        f"1. Translate EVERY line's `text` field into {language_name}. Do NOT leave any line untranslated.\n"
        f"2. Keep the same number of lines and the SAME `i` index for every line — never merge, split, reorder, or drop lines.\n"
        f"3. Preserve names, brand names, numbers, and technical terms accurately.\n"
        f"4. Keep translations natural and fluent, matching the tone of the original spoken language"
        + (f" (source language hint: {source_hint})." if source_hint else ".") + "\n"
        f"5. Return a JSON object of the form: {{\"lines\": [{{\"i\": 0, \"text\": \"...\"}}, ...]}} "
        f"with one entry per input line.\n\n"
        f"INPUT LINES (JSON):\n{json.dumps(indexed_lines, ensure_ascii=False)}"
    )

    if is_openai_provider:
        catalogue = openai_list_models(base_url, api_key)
        models_to_try: List[str] = []
        if requested_model and not requested_model.lower().startswith("gemini-"):
            models_to_try.append(requested_model)
        for needle in ("hermesagent", "gpt-", "claude", "gemini/", "auto"):
            for candidate in catalogue:
                if needle in candidate.lower() and candidate not in models_to_try:
                    models_to_try.append(candidate)
        for candidate in catalogue:
            if candidate not in models_to_try:
                models_to_try.append(candidate)
        if not models_to_try:
            models_to_try = [requested_model or "hermesagent"]
        last_err: Optional[Exception] = None
        for model_name in models_to_try[:8]:
            try:
                return openai_generate_json(
                    base_url, api_key, model_name, prompt,
                    {"type": "object", "properties": {"lines": {"type": "array"}}},
                    0.2, 420,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning(f"Translate attempt with {model_name} failed: {exc}")
        raise RuntimeError(str(last_err) if last_err else "Translation failed.")

    # Gemini path
    client = genai.Client(api_key=api_key)
    discovered = get_flash_models_for_key(client)
    models_to_try = [requested_model] if requested_model else []
    for fm in discovered + KNOWN_FLASH_MODELS:
        if fm not in models_to_try:
            models_to_try.append(fm)
    last_err = None
    for model_name in models_to_try:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                    max_output_tokens=65536 if any(v in model_name for v in ["2.0", "2.5", "3."]) else 8192,
                ),
            )
            raw_text = (getattr(resp, "text", None) or "").strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text)
            return json.loads(raw_text)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning(f"Translate attempt with {model_name} failed: {exc}")
    raise RuntimeError(str(last_err) if last_err else "Translation failed.")


def _extract_translated_map(parsed: dict) -> dict:
    """Turn a model reply like {"lines":[{"i":0,"text":"..."}]} into {index: text}."""
    translated_map: dict = {}
    for item in (parsed.get("lines") if isinstance(parsed, dict) else None) or []:
        if isinstance(item, dict) and item.get("i") is not None:
            try:
                translated_map[int(item["i"])] = str(item.get("text", "")).strip()
            except (ValueError, TypeError):
                continue
    return translated_map


@router.post("/api/translate")
async def translate_transcript(request: TranslateRequest):
    """Translate an existing transcript into the requested target language.

    Runs synchronously (non-streaming) and mirrors the analyze endpoint's
    provider selection (Gemini or any OpenAI-compatible endpoint such as 9router).
    Timestamps are preserved 1:1 so the translated transcript lines up with the
    original for SRT export and clip reconstruction.
    """
    provider = (request.provider or "gemini").strip().lower()
    is_openai_provider = provider in ("openai", "9router", "openai-compatible", "local")
    openai_base_url = normalize_openai_base_url(request.base_url or DEFAULT_9ROUTER_BASE_URL)

    api_key = (request.api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not is_openai_provider and not api_key:
        raise HTTPException(status_code=400, detail="Gemini API Key is required. Enter it in the web interface.")

    language_name = resolve_language_name(request.target_language)
    if not language_name:
        raise HTTPException(status_code=400, detail="A valid target language is required.")

    lines = request.transcript or []
    if not lines:
        raise HTTPException(status_code=400, detail="No transcript lines were provided to translate.")

    indexed_lines = [
        {
            "i": idx,
            "start": round(float(line.start), 2),
            "end": round(float(line.end), 2),
            "text": (line.text or "").strip(),
        }
        for idx, line in enumerate(lines)
    ]

    requested_model = (request.model or "gemini-2.5-flash").strip()

    try:
        parsed = await asyncio.to_thread(
            _translate_lines_sync,
            indexed_lines, language_name, request.source_language,
            provider, openai_base_url, api_key, requested_model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Translation failed: {exc}")

    translated_map = _extract_translated_map(parsed)

    translated_lines: List[TranscriptLine] = []
    for idx, line in enumerate(lines):
        new_text = translated_map.get(idx) or (line.text or "")
        translated_lines.append(TranscriptLine(
            start=float(line.start),
            end=float(line.end),
            text=new_text,
            engagement=line.engagement,
        ))

    return TranslateResponse(
        transcript=translated_lines,
        target_language=request.target_language,
        language_name=language_name,
    )


@router.post("/api/analyze")
async def analyze_video(request: AnalyzeRequest):
    """Stream real-time progress via Server-Sent Events, then deliver the final result."""

    async def stream():
        provider = (request.provider or "gemini").strip().lower()
        is_openai_provider = provider in ("openai", "9router", "openai-compatible", "local")
        openai_base_url = normalize_openai_base_url(request.base_url or DEFAULT_9ROUTER_BASE_URL)

        gemini_key = (request.api_key or os.environ.get("GEMINI_API_KEY") or '').strip()
        is_mock = gemini_key.lower() == "mock"

        if is_openai_provider:
            # OpenAI-compatible providers (9router, OpenRouter, LM Studio, ...) may
            # run locally without any key — only block when the endpoint demands one.
            if not gemini_key and not request.api_key:
                gemini_key = ""
        elif not gemini_key:
            yield _sse({"error": "Gemini API Key is required. Enter it in the web interface.", "status": 400})
            return

        # ── Step 1: Detect Source Type & Extract Metadata ───────────────────
        req_clean = request.url.strip()
        is_uploaded = False
        is_gdrive = is_google_drive_url(req_clean)
        is_generic = False
        uploaded_file_path: Optional[Path] = None

        if is_gdrive:
            yield _sse({
                "step": 1,
                "step_progress": 10,
                "overall_progress": 5,
                "stage": "Connecting to Google Drive",
                "detail": "Resolving Google Drive sharing link & preparing download...",
                "message": "Connecting to Google Drive..."
            })
            loop = asyncio.get_running_loop()
            gdrive_queue: asyncio.Queue = asyncio.Queue()

            def gdrive_progress(stage: str, detail: str, step_pct: int = 30):
                loop.call_soon_threadsafe(gdrive_queue.put_nowait, {
                    "step": 1,
                    "step_progress": step_pct,
                    "overall_progress": min(22, 5 + int(step_pct * 0.17)),
                    "stage": stage,
                    "detail": detail,
                    "message": detail
                })

            try:
                task = asyncio.create_task(
                    asyncio.to_thread(download_google_drive_video, req_clean, gdrive_progress)
                )
                while not task.done():
                    try:
                        evt = await asyncio.wait_for(gdrive_queue.get(), timeout=0.2)
                        yield _sse(evt)
                    except asyncio.TimeoutError:
                        pass
                while not gdrive_queue.empty():
                    yield _sse(gdrive_queue.get_nowait())

                uploaded_file_path = await task
                is_uploaded = True
            except Exception as e:
                yield _sse({"error": f"Failed to fetch video from Google Drive: {str(e)}", "status": 400})
                return
        elif is_generic_video_url(req_clean):
            # ── Generic site (film/streaming/embed/direct media) download path ──
            is_generic = True
            host = urlparse(req_clean).netloc or "the site"
            yield _sse({
                "step": 1,
                "step_progress": 8,
                "overall_progress": 4,
                "stage": f"Connecting to {host}",
                "detail": f"Resolving the video stream from {host} and preparing download...",
                "message": f"Connecting to {host}..."
            })
            loop = asyncio.get_running_loop()
            site_queue: asyncio.Queue = asyncio.Queue()

            def site_progress(stage: str, detail: str, step_pct: int = 30):
                loop.call_soon_threadsafe(site_queue.put_nowait, {
                    "step": 1,
                    "step_progress": step_pct,
                    "overall_progress": min(22, 4 + int(step_pct * 0.18)),
                    "stage": stage,
                    "detail": detail,
                    "message": detail
                })

            try:
                task = asyncio.create_task(
                    asyncio.to_thread(download_generic_video, req_clean, site_progress)
                )
                while not task.done():
                    try:
                        evt = await asyncio.wait_for(site_queue.get(), timeout=0.2)
                        yield _sse(evt)
                    except asyncio.TimeoutError:
                        pass
                while not site_queue.empty():
                    yield _sse(site_queue.get_nowait())

                uploaded_file_path = await task
                is_uploaded = True
            except Exception as e:
                yield _sse({"error": f"Failed to download video from this link: {str(e)}", "status": 400})
                return
        elif req_clean.startswith("upload_") or req_clean.startswith("/api/video/") or req_clean.startswith("file://") or os.path.exists(req_clean):
            is_uploaded = True
        elif (UPLOADS_DIR / os.path.basename(req_clean.split("?")[0])).exists():
            is_uploaded = True
        else:
            matches = list(UPLOADS_DIR.glob(f"*{req_clean}*"))
            if matches:
                is_uploaded = True

        if is_uploaded:
            if uploaded_file_path is None:
                if os.path.exists(req_clean):
                    uploaded_file_path = Path(req_clean)
                elif (UPLOADS_DIR / os.path.basename(req_clean.split("?")[0])).exists():
                    uploaded_file_path = UPLOADS_DIR / os.path.basename(req_clean.split("?")[0])
                elif (TEMP_DIR / os.path.basename(req_clean.split("?")[0])).exists():
                    uploaded_file_path = TEMP_DIR / os.path.basename(req_clean.split("?")[0])
                else:
                    matches = list(UPLOADS_DIR.glob(f"*{req_clean}*"))
                    if matches:
                        uploaded_file_path = matches[0]
                    else:
                        yield _sse({"error": "Uploaded video file not found on disk. Please upload again.", "status": 404})
                        return

            video_id = uploaded_file_path.stem
            canonical_url = req_clean if (is_gdrive or is_generic) else f"/api/video/{uploaded_file_path.name}"
            video_url = f"/api/video/{uploaded_file_path.name}"
            source_type = "gdrive" if is_gdrive else ("site" if is_generic else "upload")

            yield _sse({
                "step": 1,
                "step_progress": 30,
                "overall_progress": 8,
                "stage": "Inspecting Video File",
                "detail": f"Reading streams & container metadata from {uploaded_file_path.name}...",
                "message": f"Inspecting video file: {uploaded_file_path.name}..."
            })

            try:
                metadata = await asyncio.to_thread(get_video_file_metadata, uploaded_file_path)
                title = metadata.get("title") or (uploaded_file_path.stem.replace("gdrive_", "GDrive: ") if is_gdrive else uploaded_file_path.stem)
                channel = "Google Drive" if is_gdrive else "Local Upload"
                if is_generic:
                    try:
                        site_meta = await asyncio.to_thread(fetch_generic_metadata, req_clean)
                    except Exception:
                        site_meta = {}
                    if site_meta.get("title"):
                        title = site_meta["title"]
                    if site_meta.get("channel"):
                        channel = site_meta["channel"]
                    if site_meta.get("duration"):
                        metadata["duration"] = site_meta["duration"]
                duration = metadata.get("duration", 0.0)
                is_live = False
                live_status = "not_live"
                yield _sse({
                    "step": 1,
                    "step_progress": 100,
                    "overall_progress": 25,
                    "stage": "Video Verified",
                    "detail": f"Loaded video \"{title[:45]}\" ({int(duration)}s, {metadata.get('width')}x{metadata.get('height')})",
                    "message": f"Loaded video — \"{title[:45]}\" ({int(duration)}s)"
                })
            except Exception as e:
                yield _sse({"error": f"Failed to probe uploaded video metadata: {str(e)}", "status": 500})
                return

            logger.info(f"Local video loaded: title='{title}', duration={duration}s, path={uploaded_file_path}")

            # ── Step 2: Heatmap via acoustic energy ──────────────────────────
            yield _sse({
                "step": 2,
                "step_progress": 40,
                "overall_progress": 35,
                "stage": "Computing Acoustic Heatmap",
                "detail": "Analyzing audio energy envelope & speech intensity peaks...",
                "message": "Computing audio engagement curve..."
            })
            try:
                heatmap = await asyncio.to_thread(compute_audio_energy_heatmap, uploaded_file_path, duration)
                yield _sse({
                    "step": 2,
                    "step_progress": 100,
                    "overall_progress": 50,
                    "stage": "Acoustic Peaks Decoded",
                    "detail": f"Acoustic engagement heatmap parsed — {len(heatmap)} audience interest data points generated.",
                    "message": f"Acoustic energy heatmap loaded ({len(heatmap)} points)."
                })
            except Exception as e:
                logger.warning(f"Heatmap calculation error: {e}")
                heatmap = []
                yield _sse({
                    "step": 2,
                    "step_progress": 100,
                    "overall_progress": 50,
                    "stage": "Dialogue Fallback",
                    "detail": "Proceeding with full dialogue transcript analysis.",
                    "message": "Proceeding with speech transcription..."
                })

            # ── Step 3: Speech Recognition via Whisper AI ───────────────────
            if request.subtitles:
                yield _sse({
                    "step": 3,
                    "step_progress": 30,
                    "overall_progress": 55,
                    "stage": "Parsing Subtitles",
                    "detail": "Parsing custom SRT/TXT subtitle timestamps...",
                    "message": "Parsing manual subtitles..."
                })
                try:
                    transcript_lines = parse_manual_subtitles(request.subtitles, duration)
                    if not transcript_lines:
                        raise Exception("Custom subtitles parsed into empty array.")
                    yield _sse({
                        "step": 3,
                        "step_progress": 100,
                        "overall_progress": 70,
                        "stage": "Subtitles Ready",
                        "detail": f"Custom subtitles parsed — {len(transcript_lines)} timestamped lines loaded.",
                        "message": f"Custom subtitles parsed — {len(transcript_lines)} lines loaded successfully."
                    })
                except Exception as e:
                    yield _sse({"error": f"Failed to parse manual subtitles: {str(e)}", "status": 400})
                    return
            else:
                loop = asyncio.get_running_loop()
                progress_queue = asyncio.Queue()

                def whisper_progress(stage: str, detail: str, step_pct: int = 30):
                    loop.call_soon_threadsafe(progress_queue.put_nowait, {
                        "step": 3,
                        "step_progress": step_pct,
                        "overall_progress": min(68, 50 + int(step_pct * 0.2)),
                        "stage": stage,
                        "detail": detail,
                        "message": detail
                    })

                yield _sse({
                    "step": 3,
                    "step_progress": 25,
                    "overall_progress": 55,
                    "stage": "Transcribing with Whisper",
                    "detail": "Running OpenAI Whisper neural speech model on video audio track...",
                    "message": "Transcribing video dialogue with Whisper AI..."
                })

                try:
                    task = asyncio.create_task(
                        asyncio.to_thread(transcribe_local_video_file, uploaded_file_path, whisper_progress)
                    )
                    while not task.done():
                        try:
                            evt = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                            yield _sse(evt)
                        except asyncio.TimeoutError:
                            pass

                    while not progress_queue.empty():
                        yield _sse(progress_queue.get_nowait())

                    transcript_lines = await task
                    if not transcript_lines:
                        if is_mock:
                            transcript_lines = [
                                {"text": "Hello and welcome to this video.",            "start":  0.0, "duration": 3.0},
                                {"text": "Today we are looking at how this app works.",  "start":  3.0, "duration": 4.0},
                                {"text": "It finds viral hotspots and highlights them.",  "start":  7.0, "duration": 4.0},
                                {"text": "This works locally on any uploaded file.",     "start": 11.0, "duration": 3.0},
                            ]
                        else:
                            yield _sse({
                                "error": "No spoken words were detected in this video file. Ensure the video contains clear audible speech.",
                                "status": 400
                            })
                            return

                    yield _sse({
                        "step": 3,
                        "step_progress": 100,
                        "overall_progress": 70,
                        "stage": "Dialogue Transcribed",
                        "detail": f"Whisper speech-to-text complete — {len(transcript_lines)} timestamped dialogue sentences ready.",
                        "message": f"Whisper transcribed {len(transcript_lines)} dialogue segments successfully."
                    })
                except Exception as e:
                    if is_mock:
                        transcript_lines = [
                            {"text": "Sample speech from uploaded mock video.", "start": 0.0, "duration": 4.0},
                            {"text": "Highlighting viral moments automatically.", "start": 4.0, "duration": 5.0}
                        ]
                    else:
                        yield _sse({"error": f"Whisper transcription failed: {str(e)}", "status": 500})
                        return

        else:
            # ── YouTube extraction path ──────────────────────────────────────
            source_type = "youtube"
            video_url = None
            video_id = extract_video_id(request.url)
            if not video_id:
                if not is_mock:
                    yield _sse({"error": "Invalid YouTube URL or file reference. Please check and try again.", "status": 400})
                    return
                video_id = "dQw4w9WgXcQ"

            canonical_url = f"https://www.youtube.com/watch?v={video_id}"

            yield _sse({
                "step": 1,
                "step_progress": 30,
                "overall_progress": 8,
                "stage": "Connecting to YouTube",
                "detail": "Connecting to YouTube & fetching video metadata...",
                "message": "Connecting to YouTube — fetching video title and duration..."
            })

            channel = ""
            try:
                metadata = await asyncio.to_thread(fetch_video_metadata, canonical_url, request.proxy)
                title    = metadata["title"]
                channel  = metadata.get("channel", "")
                duration = metadata["duration"]
                heatmap  = metadata.get("heatmap") or []
                is_live  = metadata.get("is_live", False)
                live_status = metadata.get("live_status", "not_live")
                yield _sse({
                    "step": 1,
                    "step_progress": 100,
                    "overall_progress": 25,
                    "stage": "Video Verified",
                    "detail": f"Loaded metadata for \"{title[:45]}\" ({int(duration)}s)",
                    "message": f"Connected — \"{title[:45]}\" ({int(duration)}s)"
                })
            except Exception as e:
                if is_mock:
                    title = "Mock YouTube Video"
                    channel = "Cheat Clip Pro"
                    duration = 212.0
                    heatmap = []
                    is_live = False
                    live_status = "not_live"
                    yield _sse({
                        "step": 1,
                        "step_progress": 100,
                        "overall_progress": 25,
                        "stage": "Video Verified",
                        "detail": "Loaded mock video metadata (212s)",
                        "message": "Mock video metadata loaded"
                    })
                else:
                    msg = e.detail if isinstance(e, HTTPException) else str(e)
                    yield _sse({"error": f"Failed to fetch video details: {msg}", "status": 500})
                    return

            logger.info(f"Metadata fetched: title='{title}', duration={duration}s, heatmap_pts={len(heatmap)}")

            # ── Step 2: Heatmap ──────────────────────────────────────────────────
            yield _sse({
                "step": 2,
                "step_progress": 40,
                "overall_progress": 35,
                "stage": "Scraping Retention",
                "detail": "Extracting viewer replay telemetry and retention curve...",
                "message": "Scraping player viewer retention curve..."
            })
            if heatmap:
                yield _sse({
                    "step": 2,
                    "step_progress": 100,
                    "overall_progress": 50,
                    "stage": "Retention Decoded",
                    "detail": f"Viewer retention heatmap loaded — {len(heatmap)} audience interest data points parsed.",
                    "message": f"Viewer retention heatmap loaded — {len(heatmap)} data points scraped."
                })
            else:
                yield _sse({
                    "step": 2,
                    "step_progress": 100,
                    "overall_progress": 50,
                    "stage": "Dialogue Fallback",
                    "detail": "No heatmap curve available — relying on full transcript dialogue analysis.",
                    "message": "No heatmap available for this video — will rely on transcript content analysis."
                })

            # ── Step 3: Transcript ───────────────────────────────────────────────
            if request.subtitles:
                yield _sse({
                    "step": 3,
                    "step_progress": 30,
                    "overall_progress": 55,
                    "stage": "Parsing Subtitles",
                    "detail": "Parsing custom SRT/TXT subtitle timestamps...",
                    "message": "Parsing manual subtitles..."
                })
                try:
                    transcript_lines = parse_manual_subtitles(request.subtitles, duration)
                    if not transcript_lines:
                        raise Exception("Custom subtitles parsed into empty array.")
                    yield _sse({
                        "step": 3,
                        "step_progress": 100,
                        "overall_progress": 70,
                        "stage": "Subtitles Ready",
                        "detail": f"Custom subtitles parsed — {len(transcript_lines)} timestamped lines loaded.",
                        "message": f"Custom subtitles parsed — {len(transcript_lines)} lines loaded successfully."
                    })
                except Exception as e:
                    yield _sse({"error": f"Failed to parse manual subtitles: {str(e)}", "status": 400})
                    return
            else:
                loop = asyncio.get_running_loop()
                progress_queue = asyncio.Queue()

                def progress_callback(stage: str, detail: str, step_pct: int = 30):
                    loop.call_soon_threadsafe(progress_queue.put_nowait, {
                        "step": 3,
                        "step_progress": step_pct,
                        "overall_progress": min(68, 50 + int(step_pct * 0.2)),
                        "stage": stage,
                        "detail": detail,
                        "message": detail
                    })

                # Initial stage event
                yield _sse({
                    "step": 3,
                    "step_progress": 25,
                    "overall_progress": 55,
                    "stage": "Fetching Subtitles",
                    "detail": "Initializing multi-tier subtitle extraction pipeline...",
                    "message": "Initializing multi-tier subtitle extraction pipeline..."
                })

                try:
                    task = asyncio.create_task(
                        asyncio.to_thread(fetch_transcript, video_id, request.proxy, progress_callback)
                    )

                    while not task.done():
                        try:
                            evt = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                            yield _sse(evt)
                        except asyncio.TimeoutError:
                            pass

                    while not progress_queue.empty():
                        yield _sse(progress_queue.get_nowait())

                    transcript_lines = await task
                    yield _sse({
                        "step": 3,
                        "step_progress": 100,
                        "overall_progress": 70,
                        "stage": "Subtitles Ready",
                        "detail": f"Subtitles loaded — {len(transcript_lines)} dialogue sentences with timestamps ready.",
                        "message": f"Subtitles loaded — {len(transcript_lines)} lines parsed successfully."
                    })
                except Exception as e:
                    if is_mock:
                        transcript_lines = [
                            {"text": "Hello and welcome to this video.",            "start":  0.0, "duration": 3.0},
                            {"text": "Today we are looking at how this app works.",  "start":  3.0, "duration": 4.0},
                            {"text": "It finds viral hotspots and highlights them.",  "start":  7.0, "duration": 4.0},
                            {"text": "Most people think it's magic.",               "start": 11.0, "duration": 3.0},
                            {"text": "But it uses YouTube player heatmaps.",         "start": 14.0, "duration": 4.0},
                            {"text": "And processes them with Gemini AI models.",    "start": 18.0, "duration": 4.0},
                            {"text": "This is changing how editors crop videos.",    "start": 22.0, "duration": 5.0},
                            {"text": "If you want to grow on TikTok, try it.",      "start": 27.0, "duration": 5.0},
                            {"text": "We will explore the code next.",               "start": 32.0, "duration": 3.0},
                        ]
                        yield _sse({
                            "step": 3,
                            "step_progress": 100,
                            "overall_progress": 70,
                            "stage": "Subtitles Ready",
                            "detail": "Mock mode — 9 sample dialogue lines loaded.",
                            "message": "Mock mode — using sample transcript."
                        })
                    else:
                        # ── Offline Whisper fallback for live / post-live ──────
                        # YouTube exposes no captions for a live or just-ended
                        # broadcast, so instead of dead-ending we capture the
                        # broadcast media locally and transcribe it offline with
                        # Whisper. The captured file is then served so the clip
                        # renders (and the player) work from it too.
                        if is_live or live_status in ('is_live', 'is_upcoming', 'post_live'):
                            yield _sse({
                                "step": 3,
                                "step_progress": 30,
                                "overall_progress": 56,
                                "stage": "Live Fallback",
                                "detail": (
                                    "No captions are available for a live/recently-ended broadcast. "
                                    "Capturing the broadcast locally and transcribing it offline with Whisper AI..."
                                ),
                                "message": "No captions found — capturing broadcast audio for offline Whisper transcription..."
                            })

                            captured_path = None
                            try:
                                captured_path = await asyncio.to_thread(
                                    capture_youtube_live_media,
                                    canonical_url,
                                    UPLOADS_DIR,
                                    is_live,
                                    LIVE_FALLBACK_SECONDS,
                                    0.0,
                                    1800,
                                    duration,
                                    progress_callback,
                                )
                            except Exception as cap_exc:  # noqa: BLE001
                                logger.warning(f"Broadcast capture failed: {cap_exc}")

                            if not captured_path:
                                yield _sse({
                                    "error": (
                                        "No subtitles could be retrieved for this live/recently-ended broadcast, "
                                        "and its media could not be captured for offline transcription. Please try "
                                        "again shortly, or upload custom subtitles (.srt/.txt) to analyze this video."
                                    ),
                                    "status": 400
                                })
                                return

                            # Recompute duration from the captured file when unknown
                            try:
                                cap_meta = await asyncio.to_thread(get_video_file_metadata, captured_path)
                                if (not duration) and cap_meta.get("duration"):
                                    duration = cap_meta["duration"]
                            except Exception:  # noqa: BLE001
                                pass

                            # Transcribe the captured broadcast offline with Whisper
                            try:
                                task2 = asyncio.create_task(
                                    asyncio.to_thread(transcribe_local_video_file, captured_path, progress_callback)
                                )
                                while not task2.done():
                                    try:
                                        evt = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                                        yield _sse(evt)
                                    except asyncio.TimeoutError:
                                        pass
                                while not progress_queue.empty():
                                    yield _sse(progress_queue.get_nowait())
                                transcript_lines = await task2
                            except Exception as whisper_exc:  # noqa: BLE001
                                yield _sse({
                                    "error": f"Offline Whisper transcription of the broadcast failed: {whisper_exc}",
                                    "status": 500
                                })
                                return

                            if not transcript_lines:
                                yield _sse({
                                    "error": (
                                        "The captured broadcast audio contained no detectable speech. "
                                        "Upload custom subtitles (.srt/.txt) to analyze this video."
                                    ),
                                    "status": 400
                                })
                                return

                            # Serve the captured file so clips render from it locally.
                            # Keep source_type "youtube" so history/thumbnails still
                            # key off the YouTube video_id; the player and the render
                            # pipeline both prefer video_url, which now points at the
                            # locally captured broadcast.
                            video_url = f"/api/video/{Path(captured_path).name}"
                            yield _sse({
                                "step": 3,
                                "step_progress": 100,
                                "overall_progress": 70,
                                "stage": "Subtitles Ready",
                                "detail": f"Offline Whisper transcribed {len(transcript_lines)} dialogue segments from the captured broadcast.",
                                "message": f"Broadcast transcribed offline — {len(transcript_lines)} lines ready."
                            })
                        else:
                            msg = e.detail if isinstance(e, HTTPException) else str(e)
                            yield _sse({"error": msg, "status": 400})
                            return

        # Estimate duration from transcript if missing
        if duration == 0.0 and transcript_lines:
            last = transcript_lines[-1]
            duration = last.get("start", 0.0) + last.get("duration", 0.0)

        # Slice transcript based on custom search range if provided
        start_bound = 0.0
        end_bound = duration
        if request.range_start is not None or request.range_end is not None:
            start_bound = request.range_start if request.range_start is not None else 0.0
            end_bound = request.range_end if request.range_end is not None else duration

            if start_bound < 0.0:
                start_bound = 0.0
            if end_bound > duration:
                end_bound = duration

            if start_bound >= end_bound:
                yield _sse({"error": "Invalid search range: start time must be less than end time.", "status": 400})
                return

            filtered_lines = []
            for line in transcript_lines:
                ls = line.get("start", 0.0)
                le = ls + line.get("duration", 0.0)
                if max(ls, start_bound) < min(le, end_bound):
                    filtered_lines.append(line)
            
            transcript_lines = filtered_lines
            if not transcript_lines:
                yield _sse({"error": f"No subtitles found in the specified range {start_bound}s to {end_bound}s.", "status": 400})
                return
            
            duration = end_bound - start_bound
            logger.info(f"Filtered transcript to custom range: {start_bound}s to {end_bound}s (duration: {duration}s)")

        # Enrich transcript with heatmap engagement scores
        enriched_transcript = []
        for line in transcript_lines:
            ls   = line.get("start", 0.0)
            ld   = line.get("duration", 0.0)
            le   = ls + ld
            score = get_average_heatmap_value(ls, le, heatmap)
            enriched_transcript.append({
                "start":      round(ls, 2),
                "end":        round(le, 2),
                "text":       line.get("text", ""),
                "engagement": round(score, 3)
            })

        # ── Mock short-circuit ───────────────────────────────────────────────
        if is_mock:
            mock_stages = [
                ("Context Assembly", "Aligning 9 transcript dialogue lines with retention telemetry...", 30, 78),
                ("Viral Hook & Curiosity Detection", "Scanning transcript dialogue for viral hooks & curiosity gaps...", 65, 88),
                ("Virality Scoring & Selection", "Calculating virality coefficients and formatting clip candidates...", 92, 95),
            ]
            for s_name, s_detail, s_prog, o_prog in mock_stages:
                yield _sse({
                    "step": 4,
                    "step_progress": s_prog,
                    "overall_progress": o_prog,
                    "stage": s_name,
                    "detail": s_detail,
                    "model": "gemini-2.5-flash (Mock)",
                    "message": f"Mock AI ({s_name}): {s_detail}"
                })
                await asyncio.sleep(0.7)

            mock_clips = [
                ViralClip(title="Finding hotspots using heatmaps",  start_time=11.0, end_time=22.0, hook_time=14.0, virality_score=95,
                          key_quotes=["Uses YouTube player heatmaps.", "Processes using Gemini AI."],
                          transcript="Most people think it's magic. But it uses YouTube player heatmaps.",
                          title_suggestion="Unlock Video Virality Secrets",
                          caption_suggestion="Stop guessing what works! Here's how to use heatmaps to find viral hotspots in seconds. 🔥",
                          hashtag_suggestion="#viralclips #videoediting #heatmaps #aitools"),
                ViralClip(title="Grow on TikTok or Reels",          start_time=22.0, end_time=32.0, hook_time=27.0, virality_score=88,
                          key_quotes=["Changing how editors crop videos.", "If you want to grow on TikTok, try it."],
                          transcript="This is changing how editors crop videos. If you want to grow on TikTok, try it.",
                          title_suggestion="The Ultimate TikTok Growth Hack",
                          caption_suggestion="Want to scale your TikTok views? This tool will revolutionize your workflow. 🚀",
                          hashtag_suggestion="#tiktokgrowth #reels #shorts #editingtips"),
                ViralClip(title="Introductory overview of the tool", start_time=0.0,  end_time=11.0, hook_time=3.0, virality_score=72,
                          key_quotes=["Hello and welcome.", "Finds viral hotspots."],
                          transcript="Hello and welcome. It finds viral hotspots and highlights them.",
                          title_suggestion="Meet Cheat Clip Pro AI",
                          caption_suggestion="Say hello to your new AI co-editor. Find the absolute best parts of any video instantly.",
                          hashtag_suggestion="#cheatclippro #aiediting #growthmindset"),
            ]
            mock_heatmap = [
                HeatmapPoint(start_time=i*10.0, end_time=(i+1)*10.0,
                             value=0.2 + (0.6 if i in [2,5,8,12,16] else 0.1))
                for i in range(20)
            ] if not heatmap else [
                HeatmapPoint(start_time=float(pt.get('start_time',0.0)),
                             end_time=float(pt.get('end_time',0.0)),
                             value=float(pt.get('value',0.0)))
                for pt in heatmap
            ]
            result = AnalyzeResponse(
                video_id=video_id, title=title, channel=channel, duration=duration or 200.0,
                heatmap=mock_heatmap,
                summary="Mock analysis: this video explains how CHEAT CLIP PRO works. #aitools #videoediting #productivity",
                clips=mock_clips,
                model="Mock Gemini"
            )
            yield _sse({
                "step": 4,
                "step_progress": 100,
                "overall_progress": 100,
                "stage": "Analysis Complete",
                "detail": "Generated 3 viral clip candidates successfully.",
                "done": True,
                "result": result.model_dump()
            })
            return

        is_long_video = duration > 3600
        is_auto_clip_count = False
        target_count_num = None
        if request.target_clip_count is not None:
            if isinstance(request.target_clip_count, str) and request.target_clip_count.lower() == 'auto':
                is_auto_clip_count = True
            else:
                try:
                    target_count_num = int(request.target_clip_count)
                    if target_count_num <= 0:
                        is_auto_clip_count = True
                except (ValueError, TypeError):
                    is_auto_clip_count = True
        else:
            is_auto_clip_count = True

        if not is_auto_clip_count and target_count_num:
            N = target_count_num
            if N <= 5:
                min_clips = max(1, N - 1)
                max_clips = N + 2
            elif N <= 10:
                min_clips = max(1, N - 2)
                max_clips = N + 3
            else:
                min_clips = max(1, N - 5)
                max_clips = N + 5
            clip_range = f"{min_clips}-{max_clips}"
            clip_count_instruction = (
                f"TARGET CLIP COUNT: Approximately {clip_range} clips. "
                f"Identify the highest-quality, most viral segments within this range."
            )
        else:
            clip_range = "up to around 200 (AI-determined based on interesting topics)"
            clip_count_instruction = (
                "DYNAMIC AUTO CLIP COUNT & TOPIC CURATION RULES:\n"
                "- You (the AI editor) decide the total number of clips to extract based on how many genuinely interesting, high-value, and viral topics exist in this video.\n"
                "- Upper Constraint Ceiling: Extract up to around 200 clips maximum (no need to reach exactly 200; extract as many as the video's content genuinely justifies, up to approximately 200 clips).\n"
                "- High-Interest Standalone Topics Required: Every single clip MUST focus on an interesting, distinct, and compelling topic, idea, debate, story, funny moment, or revelation. Do NOT produce repetitive, weak, or trivial filler clips just to inflate the count. Only create clips for moments that would actually captivate an audience."
            )

        # ── Step 4: Build prompt & Detect Language ─────────────────────────────
        detected_lang = detect_transcript_language(enriched_transcript, title)
        lang_code = detected_lang.get('code', 'en')
        lang_name = detected_lang.get('name', 'English')
        logger.info(f"Detected video language: {lang_name} ({lang_code}) - confidence {detected_lang.get('confidence', 0.0)}")

        # Optional user-requested output language for clip titles/summary/captions.
        # 'auto' (or empty) keeps the video's own detected language (zero-translation).
        requested_title_lang = resolve_language_name(request.title_language)
        output_lang_name = requested_title_lang or lang_name
        output_lang_code = (request.title_language or '').strip() or lang_code
        is_title_translation = bool(requested_title_lang) and requested_title_lang.lower() != lang_name.lower()
        if is_title_translation:
            logger.info(f"Title/summary language override requested: {lang_name} -> {output_lang_name}")

        transcript_dump = []
        for line in enriched_transcript:
            eng = f"|{line['engagement']:.2f}" if heatmap and line['engagement'] > 0 else ""
            transcript_dump.append(f"{line['start']:.1f}|{line['end']:.1f}{eng} {line['text']}")

        MAX_LINES = 8000 if is_long_video else 4000
        if len(transcript_dump) > MAX_LINES:
            logger.warning(f"Transcript {len(transcript_dump)} lines — truncating to {MAX_LINES}.")
            transcript_dump = transcript_dump[:MAX_LINES]

        transcript_text = "\n".join(transcript_dump)

        # ── TikTok source override: clips must be 30s–60s (user requirement) ──
        _netloc = (urlparse(req_clean).netloc or "").lower()
        is_tiktok_source = _netloc.endswith("tiktok.com") or ".tiktok.com" in _netloc

        is_auto_duration = str(request.duration).lower() == "auto"
        if is_tiktok_source:
            dur_range = "30-60s"
            duration_instruction = (
                "TikTok CLIP DURATION RULE (STRICT — HIGHEST PRIORITY, OVERRIDES ALL OTHER DURATION SETTINGS):\n"
                "- This video comes from TikTok, so EVERY clip MUST be between 30 seconds and 60 seconds (1 minute).\n"
                "- Absolute minimum: 30 seconds. Absolute maximum: 60 seconds. NEVER produce a clip shorter than 30s or longer than 60s.\n"
                "- Prefer moments long enough to naturally fill the 30–60s window (a complete story, exchange, or explanation).\n"
                "- Always begin and end at complete sentence boundaries; never cut mid-sentence or mid-thought.\n"
                "- Double-check each clip's (end - start) is >= 30 and <= 60 before returning it."
            )
        elif is_auto_duration:
            dur_range = "Auto dynamic length (~15s to ~90s max)"
            duration_instruction = (
                "DYNAMIC AUTO CLIP DURATION GUIDELINES:\n"
                "- Intelligently adjust the duration of each individual clip based on its natural narrative flow and context so that NO CONTEXT, setup, premise, punchline, or explanation is cut off mid-thought.\n"
                "- Generate variant clip durations naturally: ranging flexibly from approximately 15 seconds up to around 90 seconds (1 minute 30 seconds maximum). Do NOT force all clips to be the same length (some can be ~20-30s, some ~40-60s, and deeper discussions or complex stories can be up to around 90s).\n"
                "- Strict maximum constraint: No clip should ever exceed 90 seconds (1 minute 30 seconds). Always ensure clips begin and end cleanly at complete sentence boundaries without mid-word or mid-sentence cuts."
            )
        else:
            dur_target = {"15s": "10-20s", "30s": "20-40s", "60s": "45-75s"}.get(request.duration, "20-40s")
            dur_range = dur_target
            duration_instruction = f"Target clip length: {dur_target}. Keep clips tightly focused around this duration."

        heatmap_note = (
            "Columns: start|end|audience_interest(0-1). Prioritise high-interest peaks."
            if heatmap else
            "No audience interest data. Use content hooks, energy, and story arcs."
        )
        focus_instruction = ""
        if request.custom_prompt and request.custom_prompt.strip():
            focus_instruction = (
                f"CRITICAL USER SEARCH FOCUS:\n"
                f"The user specifically wants you to find clips matching the following query/theme: \"{request.custom_prompt.strip()}\".\n"
                f"Prioritize and tailor your selection of viral clips to fit this request, while still ensuring they make good standalone clips.\n"
                f"IMPORTANT: Regardless of the language this query was typed in, your generated titles, summaries, quotes, and captions MUST REMAIN IN THE VIDEO'S SPOKEN LANGUAGE ({lang_name}). DO NOT translate the video's content to match the language of the search query.\n\n"
            )

        channel_context = f"- Channel / Host / Creator: {channel}\n" if channel else ""

        if is_title_translation:
            language_block = (
                f"================================================================================\n"
                f"CRITICAL MANDATORY OUTPUT LANGUAGE RULE:\n"
                f"================================================================================\n"
                f"THE MAIN SPOKEN LANGUAGE OF THIS VIDEO IS: {lang_name.upper()} (Language Code: '{lang_code}').\n"
                f"THE USER HAS REQUESTED THAT ALL GENERATED TEXT BE WRITTEN IN: {output_lang_name.upper()}.\n\n"
                f"1. You MUST write the following fields 100% in {output_lang_name.upper()} (translate them if the video is in another language):\n"
                f"   - `summary`: In {output_lang_name.upper()}.\n"
                f"   - `title`: Catchy title in {output_lang_name.upper()}, max 8 words.\n"
                f"   - `title_suggestion`: Alternative title in {output_lang_name.upper()}.\n"
                f"   - `caption_suggestion`: Engaging social caption in {output_lang_name.upper()}.\n"
                f"   - `hashtag_suggestion`: Relevant hashtags in {output_lang_name.upper()}.\n"
                f"2. `key_quotes` MUST remain VERBATIM quotes exactly as spoken in the ORIGINAL transcript language ({lang_name.upper()}). DO NOT translate key_quotes.\n"
                f"3. Translate meaning faithfully and naturally; never output any third language.\n"
                f"================================================================================\n\n"
            )
        else:
            language_block = (
                f"================================================================================\n"
                f"CRITICAL MANDATORY LANGUAGE RULE (ZERO TRANSLATION):\n"
                f"================================================================================\n"
                f"THE MAIN SPOKEN LANGUAGE OF THIS VIDEO IS: {lang_name.upper()} (Language Code: '{lang_code}').\n"
                f"YOU MUST GENERATE 100% OF ALL CONTENT STRICTLY AND EXCLUSIVELY IN {lang_name.upper()}.\n\n"
                f"1. NEVER TRANSLATE TO ANY OTHER LANGUAGE!\n"
                f"   - If the video is in English -> ALL titles, title suggestions, summaries, captions, hashtags, and quotes MUST BE 100% IN ENGLISH. Do NOT write Indonesian, Spanish, or any other language!\n"
                f"   - If the video is in Indonesian -> ALL titles, title suggestions, summaries, captions, hashtags, and quotes MUST BE 100% IN INDONESIAN. Do NOT write English, Arabic, or any other language!\n"
                f"   - CRITICAL GUARD FOR INDONESIAN VIDEOS: If speakers use occasional Arabic loanwords or Islamic greetings/phrases (e.g., 'Assalamu'alaikum', 'Bismillah', 'Alhamdulillah', 'Insya Allah', or Arabic quotes), DO NOT TRANSLATE INTO ARABIC! The video language is 100% BAHASA INDONESIA. All titles, suggestions, and captions MUST be written strictly in BAHASA INDONESIA.\n"
                f"   - If the video is in another language -> ALL output MUST strictly match that language.\n"
                f"   - Even if the user's prompt or search query was written in another language, your output MUST REMAIN 100% IN {lang_name.upper()}.\n"
                f"2. Strict field requirements in {lang_name.upper()}:\n"
                f"   - `summary`: In {lang_name.upper()}.\n"
                f"   - `title`: Catchy title in {lang_name.upper()}, max 8 words.\n"
                f"   - `title_suggestion`: Alternative title in {lang_name.upper()}.\n"
                f"   - `caption_suggestion`: Engaging social caption in {lang_name.upper()}.\n"
                f"   - `hashtag_suggestion`: Relevant hashtags in {lang_name.upper()}.\n"
                f"   - `key_quotes`: MUST be verbatim spoken quotes directly from the transcript in {lang_name.upper()}.\n"
                f"================================================================================\n\n"
            )

        prompt = (
            f"You are an expert viral video clip editor finding top clip candidates for TikTok, Instagram Reels, and YouTube Shorts.\n"
            f"Analyze this YouTube video transcript and find {clip_range} high-performing, standalone clip candidates.\n\n"
            f"VIDEO CONTEXT:\n"
            f"- Video Title: {title}\n"
            f"{channel_context}"
            f"- Video Duration: {int(start_bound)}s to {int(end_bound)}s (Total: {int(duration)}s) | Target clip length: {dur_range}\n"
            f"- Heatmap: {heatmap_note}\n"
            f"- DETECTED VIDEO SPOKEN LANGUAGE: {lang_name} (Code: '{lang_code}')\n\n"
            f"{language_block}"
            f"{duration_instruction}\n\n"
            f"{clip_count_instruction}\n\n"
            f"{focus_instruction}"
            f"CRITICAL TITLE & ATTRIBUTION RULES (NO FIRST-PERSON 'I' OR 'ME'):\n"
            f"1. NEVER write clip titles or title suggestions using first-person pronouns ('I', 'me', 'my', 'mine', 'myself', or equivalents in other languages such as 'saya', 'aku', 'gue')!\n"
            f"2. The user posting or curating these clips is a third-party editor, NOT the person speaking in the video. Titles must NEVER make it look like the clip is expressing the user's personal opinion, story, or reaction (e.g. NEVER write 'Why I think this is bad', 'How I made $100K', 'My biggest mistake', or 'I was shocked' / in Indonesian: NEVER 'Kenapa saya...', 'Cara aku...', 'Opini gue...').\n"
            f"3. ALWAYS attribute statements to the context of the video and the actual person speaking:\n"
            f"   - Use the actual name of the speaker, host, or guest from the video title, channel name ({channel or 'Host'}), or transcript dialogue (e.g. '{channel or 'Speaker'} Explains...', 'Why {channel or 'Host'} Shocked Fans', '[Name] Reveals The Truth' / in Indonesian: '{channel or 'Host'} Menjelaskan...', 'Alasan {channel or 'Host'} Mengungkapkan...').\n"
            f"   - If the speaker's name is not explicitly stated, use their role or descriptive title (e.g. 'Host Reacts...', 'CEO Reveals...', 'Expert Explains...', 'Guest Breaks Down...' / in Indonesian: 'Host Menjelaskan...', 'Pakar Membongkar...').\n"
            f"   - Or use objective, curiosity-driven framing (e.g. 'The Real Truth About...', 'How To Master...', 'Why Most People Fail At...', 'The Harsh Reality of...' / in Indonesian: 'Fakta Sebenarnya Tentang...', 'Cara Menguasai...').\n"
            f"4. Keep titles snappy, viral, engaging, and under 8 words.\n\n"
            f"Transcript (start|end[|interest] text):\n---\n{transcript_text}\n---\n\n"
            f"Rules: use exact seconds from transcript; clips must start/end at sentence boundaries; do not overlap.\n"
            f"Return clips sorted by virality_score desc."
        )

        requested_model = (request.model or 'gemini-2.5-flash').strip()
        if not is_openai_provider and any(dep in requested_model.lower() for dep in ['gemini-1.0', 'gemini-pro-vision']):
            logger.info(f"Requested model '{requested_model}' is outdated. Upgrading to gemini-2.5-flash.")
            requested_model = 'gemini-2.5-flash'

        yield _sse({
            "step": 4,
            "step_progress": 10,
            "overall_progress": 72,
            "stage": "Language & Context Assembly",
            "detail": f"Source language: {lang_name} ({lang_code}). Aligning {len(transcript_dump)} dialogue segments for {requested_model}...",
            "model": requested_model,
            "message": f"Verified language: {lang_name}. Zero-translation rule enforced for {requested_model}."
        })

        # ── Step 4: AI call with dynamic fallback models and retry ───────────
        client = None
        models_to_try: List[str] = []

        if is_openai_provider:
            # Any OpenAI-compatible endpoint (9router by default). The catalogue can
            # hold hundreds of models, so fall back to a few known-good entries.
            catalogue = await asyncio.to_thread(openai_list_models, openai_base_url, gemini_key)
            preferred = []
            # A bare 'gemini-2.5-flash' is a Gemini-native name and is meaningless
            # for an OpenAI-compatible router — skip it unless it carries a prefix.
            if requested_model and not requested_model.lower().startswith('gemini-'):
                preferred.append(requested_model)
            for needle in ('hermesagent', 'gpt-', 'claude', 'gemini/', 'auto'):
                for candidate in catalogue:
                    if needle in candidate.lower() and candidate not in preferred:
                        preferred.append(candidate)
            for candidate in catalogue:
                if candidate not in preferred:
                    preferred.append(candidate)
            models_to_try = preferred[:8] or [requested_model or 'hermesagent']
            if not requested_model or requested_model.lower().startswith('gemini-'):
                requested_model = models_to_try[0]
            logger.info(f"OpenAI-compatible fallback chain prepared ({openai_base_url}): {models_to_try}")
        else:
            client = genai.Client(api_key=gemini_key)
            # Discover all available Flash models for the user's API key
            discovered_flash = await asyncio.to_thread(get_flash_models_for_key, client)
            # Build models_to_try:
            # 1. Start with the requested model
            # 2. Append all discovered and known flash models in version descending order
            models_to_try = [requested_model]
            for fm in discovered_flash:
                if fm not in models_to_try:
                    models_to_try.append(fm)
            for km in KNOWN_FLASH_MODELS:
                if km not in models_to_try:
                    models_to_try.append(km)
            logger.info(f"Flash fallback chain prepared: {models_to_try}")

        response = None
        last_error = None
        encountered_quota_error = None
        analysis_data = None
        successful_model = None

        for idx, model_name in enumerate(models_to_try):
            next_model_hint = models_to_try[idx + 1] if idx + 1 < len(models_to_try) else None
            MAX_RETRIES = 2
            
            for attempt in range(MAX_RETRIES):
                if attempt > 0:
                    wait = 2
                    yield _sse({
                        "step": 4,
                        "step_progress": 25,
                        "overall_progress": 75,
                        "stage": "Transient Retry",
                        "detail": f"{model_name} busy — waiting {wait}s before retry ({attempt + 1}/{MAX_RETRIES})...",
                        "model": model_name,
                        "message": f"{model_name} is busy — waiting {wait}s before retry {attempt + 1}/{MAX_RETRIES}..."
                    })
                    await asyncio.sleep(wait)
                
                yield _sse({
                    "step": 4,
                    "step_progress": 18,
                    "overall_progress": 74,
                    "stage": "Neural Model Dispatch",
                    "detail": f"Dispatched {len(transcript_dump)} lines to {model_name} (attempt {attempt + 1})...",
                    "model": model_name,
                    "message": f"Calling {model_name} (attempt {attempt + 1}/{MAX_RETRIES})..."
                })
                
                # Execute AI call with heartbeat to keep mobile connection alive and show live stages
                if is_openai_provider:
                    task = asyncio.create_task(asyncio.to_thread(
                        openai_generate_json,
                        openai_base_url,
                        gemini_key,
                        model_name,
                        prompt,
                        VideoAnalysis.model_json_schema(),
                        0.2,
                        420,
                    ))
                else:
                    task = asyncio.create_task(asyncio.to_thread(
                        client.models.generate_content,
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=VideoAnalysis,
                            temperature=0.2,
                            max_output_tokens=65536 if any(v in model_name for v in ['2.0', '2.5', '3.']) else 8192,
                        )
                    ))
                
                call_start = asyncio.get_event_loop().time()
                while not task.done():
                    done, _ = await asyncio.wait([task], timeout=2.0)
                    if not done:
                        elapsed = int(asyncio.get_event_loop().time() - call_start)
                        
                        if elapsed < 5:
                            stage = "Neural Context Loading"
                            detail = f"Transmitting {len(transcript_dump)} timestamped dialogue segments to {model_name}..."
                            step_prog = min(35, 12 + elapsed * 4)
                        elif elapsed < 12:
                            stage = "Retention Spike Cross-Analysis"
                            detail = f"Correlating viewer retention peaks against speaker dialogue to isolate viral moments..."
                            step_prog = min(55, 35 + int((elapsed - 5) * 3))
                        elif elapsed < 20:
                            stage = "Viral Hook & Curiosity Detection"
                            detail = f"Scanning transcript dialogue for opening hooks, punchlines, controversial takes & emotional peaks..."
                            step_prog = min(72, 55 + int((elapsed - 12) * 2.2))
                        elif elapsed < 30:
                            stage = "Coherence & Sentence Boundary Snapping"
                            detail = f"Ensuring clip candidates start and end naturally on sentence boundaries without mid-word cuts..."
                            step_prog = min(85, 72 + int((elapsed - 20) * 1.3))
                        elif elapsed < 42:
                            stage = "Virality Scoring & Selection"
                            display_clip_count = "all high-value" if is_auto_clip_count else f"the top {clip_range}"
                            detail = f"Calculating virality coefficients (1-100) and selecting {display_clip_count} highest potential clips..."
                            step_prog = min(92, 85 + int((elapsed - 30) * 0.7))
                        else:
                            stage = "Social Media Metadata Synthesis"
                            detail = f"Drafting attention-grabbing titles, social captions, and targeted hashtags ({elapsed}s)..."
                            step_prog = min(95, 92 + min(3, int((elapsed - 42) * 0.3)))

                        overall_prog = 70 + int(step_prog * 0.28)
                        yield _sse({
                            "step": 4,
                            "keepalive": True,
                            "step_progress": step_prog,
                            "overall_progress": overall_prog,
                            "stage": stage,
                            "detail": detail,
                            "model": model_name,
                            "elapsed": elapsed,
                            "message": f"[{model_name} | {elapsed}s] {stage}: {detail}"
                        })
                
                try:
                    resp_candidate = await task
                    last_error = None
                    
                    # Parse structured response
                    parsed_data = None
                    if is_openai_provider:
                        # openai_generate_json already returns a plain dict.
                        if isinstance(resp_candidate, dict) and resp_candidate:
                            raw_clips = resp_candidate.get("clips") or []
                            parsed_data = {
                                "summary": resp_candidate.get("summary", "") or "",
                                "clips": [
                                    {
                                        "title": sanitize_first_person_title(c.get("title", ""), channel, lang=lang_code),
                                        "start_time": float(c.get("start_time", 0.0) or 0.0),
                                        "end_time": float(c.get("end_time", 0.0) or 0.0),
                                        "hook_time": c.get("hook_time"),
                                        "virality_score": int(c.get("virality_score", 0) or 0),
                                        "key_quotes": c.get("key_quotes") or [],
                                        "title_suggestion": sanitize_first_person_title(c.get("title_suggestion", ""), channel, lang=lang_code),
                                        "caption_suggestion": c.get("caption_suggestion", "") or "",
                                        "hashtag_suggestion": c.get("hashtag_suggestion", "") or "",
                                    }
                                    for c in raw_clips if isinstance(c, dict)
                                ]
                            }
                    elif hasattr(resp_candidate, 'parsed') and resp_candidate.parsed is not None:
                        parsed = resp_candidate.parsed
                        parsed_data = {
                            "summary": getattr(parsed, 'summary', ''),
                            "clips": [
                                {
                                    "title": sanitize_first_person_title(getattr(c, 'title', ''), channel, lang=lang_code),
                                    "start_time": getattr(c, 'start_time', 0.0),
                                    "end_time": getattr(c, 'end_time', 0.0),
                                    "hook_time": getattr(c, 'hook_time', None),
                                    "virality_score": getattr(c, 'virality_score', 0),
                                    "key_quotes": getattr(c, 'key_quotes', []),
                                    "title_suggestion": sanitize_first_person_title(getattr(c, 'title_suggestion', ''), channel, lang=lang_code),
                                    "caption_suggestion": getattr(c, 'caption_suggestion', ''),
                                    "hashtag_suggestion": getattr(c, 'hashtag_suggestion', ''),
                                }
                                for c in (getattr(parsed, 'clips', []) or [])
                            ]
                        }
                    elif getattr(resp_candidate, 'text', None):
                        raw_text = resp_candidate.text.strip()
                        if raw_text.startswith("```"):
                            raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                            raw_text = re.sub(r"\n?```$", "", raw_text)
                        try:
                            parsed_data = json.loads(raw_text)
                        except Exception as json_err:
                            logger.warning(f"JSON parsing error from {model_name}: {json_err}")
                            parsed_data = None

                    if parsed_data is not None:
                        clips_found = len(parsed_data.get('clips', []))
                        if clips_found == 0 and next_model_hint is not None:
                            logger.warning(f"{model_name} returned 0 clips. Will try next flash model {next_model_hint}...")
                            yield _sse({
                                "step": 4,
                                "step_progress": 40,
                                "overall_progress": 78,
                                "stage": "Flash Model Fallback",
                                "detail": f"{model_name} returned 0 clips — switching to {next_model_hint} for deeper extraction...",
                                "model": next_model_hint,
                                "message": f"{model_name} returned 0 clips — switching to {next_model_hint}..."
                            })
                            last_error = Exception(f"{model_name} returned 0 clips")
                            break
                        
                        response = resp_candidate
                        analysis_data = parsed_data
                        successful_model = model_name
                        break
                    else:
                        last_error = Exception(f"{model_name} returned empty or unparseable response")
                        break
                        
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    logger.warning(f"Error from {model_name} (attempt {attempt + 1}): {e}")
                    
                    if any(x in err_str for x in ('429', 'quota', 'resource exhausted', 'rate limit')):
                        encountered_quota_error = e
                        break

                    if any(x in err_str for x in ('404', 'not found', 'not supported')):
                        break
                    
                    is_server_busy = any(x in err_str for x in ('503', 'unavailable', 'overloaded', '500', 'internal'))
                    if not is_server_busy:
                        break
            
            if analysis_data is not None and response is not None:
                break
                
            if next_model_hint is not None:
                err_summary = "quota reached" if any(x in str(last_error).lower() for x in ('429', 'quota', 'rate limit')) else \
                              "not available or deprecated" if "404" in str(last_error) else \
                              "temporarily busy"
                yield _sse({
                    "step": 4,
                    "step_progress": 35,
                    "overall_progress": 76,
                    "stage": "Flash Fallback",
                    "detail": f"{model_name} {err_summary} — switching to fallback {next_model_hint}...",
                    "model": next_model_hint,
                    "message": f"{model_name} {err_summary} — switching to flash fallback model {next_model_hint}..."
                })

        if analysis_data is None:
            # If any model in the fallback chain suffered quota exhaustion, prioritize showing the quota explanation
            error_to_report = encountered_quota_error or last_error
            provider_label = "the OpenAI-compatible provider" if is_openai_provider else "Gemini"
            if error_to_report is not None:
                err_str = str(error_to_report).lower()
                if any(x in err_str for x in ('429', 'quota', 'resource exhausted', 'rate limit', 'insufficient')):
                    yield _sse({
                        "error": f"Quota/credit limit reached across all available models on {provider_label}. Please change your API key, use a different model, or wait 30–60 seconds before trying again.",
                        "status": 429
                    })
                elif any(x in err_str for x in ('503', 'unavailable', 'overloaded')):
                    yield _sse({
                        "error": f"The AI servers behind {provider_label} are currently experiencing high demand across all models. Please try a different model or wait a few moments and try again.",
                        "status": 503
                    })
                elif any(x in err_str for x in ('401', '403', 'api_key', 'invalid', 'permission', 'unauthorized')):
                    yield _sse({
                        "error": f"Invalid or rejected API key for {provider_label}. Please check your API key and try again.",
                        "status": 401
                    })
                elif any(x in err_str for x in ('404', 'not found', 'not supported')):
                    models_preview = ', '.join(models_to_try[:3])
                    yield _sse({
                        "error": f"All tested models ({models_preview}...) were unavailable or not supported on {provider_label}. Please pick another model or check your provider settings.",
                        "status": 404
                    })
                else:
                    logger.error(f"AI error after all fallback models: {error_to_report}")
                    yield _sse({
                        "error": f"AI analysis failed across all available models on {provider_label} ({str(error_to_report)}). Please try a different model or check your provider settings.",
                        "status": 500
                    })
            else:
                yield _sse({
                    "error": f"No response received after trying all available models on {provider_label}. Please try again in a few moments.",
                    "status": 500
                })
            return

        # Fallback clip synthesis if 0 clips were returned after all models
        if len(analysis_data.get('clips', [])) == 0 and enriched_transcript:
            logger.info("Generating fallback clips from heatmap and transcript segments...")
            sorted_lines = sorted(enriched_transcript, key=lambda l: l.get('engagement', 0.0), reverse=True)
            candidate_starts = []
            for l in sorted_lines:
                s = l['start']
                if not any(abs(s - existing) < 25.0 for existing in candidate_starts):
                    candidate_starts.append(s)
                if len(candidate_starts) >= 5:
                    break
            
            fallback_clips_list = []
            for i, st in enumerate(candidate_starts):
                if is_tiktok_source:
                    target_len = 45.0
                elif request.duration == "15s":
                    target_len = 15.0
                elif request.duration == "60s":
                    target_len = 60.0
                elif request.duration == "auto":
                    auto_lens = [25.0, 45.0, 75.0, 35.0, 60.0, 85.0, 20.0, 50.0]
                    target_len = auto_lens[i % len(auto_lens)]
                else:
                    target_len = 30.0
                et = min(duration, st + min(90.0, target_len))
                seg_lines = [l['text'] for l in enriched_transcript if max(l['start'], st) < min(l['end'], et)]
                seg_text = " ".join(seg_lines).strip()
                preview = seg_text[:60] + "..." if len(seg_text) > 60 else seg_text or f"Viral Highlight #{i+1}"
                if output_lang_code == 'id':
                    fallback_clips_list.append({
                        "title": f"Momen Menarik #{i+1}",
                        "start_time": st,
                        "end_time": et,
                        "hook_time": st,
                        "virality_score": max(70, int(95 - i * 5)),
                        "key_quotes": [seg_text[:80]] if seg_text else [],
                        "title_suggestion": f"Cuplikan Pilihan #{i+1}",
                        "caption_suggestion": f"Momen terbaik dari video: {preview} #viral #trending",
                        "hashtag_suggestion": "#viral #shorts #trending"
                    })
                elif output_lang_code == 'es':
                    fallback_clips_list.append({
                        "title": f"Momento Destacado #{i+1}",
                        "start_time": st,
                        "end_time": et,
                        "hook_time": st,
                        "virality_score": max(70, int(95 - i * 5)),
                        "key_quotes": [seg_text[:80]] if seg_text else [],
                        "title_suggestion": f"Momento Imperdible #{i+1}",
                        "caption_suggestion": f"Momento clave del video: {preview} #viral #trending",
                        "hashtag_suggestion": "#viral #shorts #trending"
                    })
                else:
                    fallback_clips_list.append({
                        "title": f"Key Highlight #{i+1}",
                        "start_time": st,
                        "end_time": et,
                        "hook_time": st,
                        "virality_score": max(70, int(95 - i * 5)),
                        "key_quotes": [seg_text[:80]] if seg_text else [],
                        "title_suggestion": f"Must Watch Moment #{i+1}",
                        "caption_suggestion": f"Key highlight from video: {preview} #viral #trending",
                        "hashtag_suggestion": "#viral #shorts #trending"
                    })
            analysis_data['clips'] = fallback_clips_list
            if not analysis_data.get('summary'):
                if output_lang_code == 'id':
                    analysis_data['summary'] = f"Analisis video \"{title}\" menemukan {len(fallback_clips_list)} segmen cuplikan pilihan. #viral #highlights"
                elif output_lang_code == 'es':
                    analysis_data['summary'] = f"Análisis de \"{title}\" identificando {len(fallback_clips_list)} segmentos clave. #viral #highlights"
                else:
                    analysis_data['summary'] = f"Analysis of \"{title}\" identifying {len(fallback_clips_list)} key segments. #viral #highlights"

        clip_count = len(analysis_data.get('clips', []))
        yield _sse({
            "step": 4,
            "step_progress": 98,
            "overall_progress": 98,
            "stage": "Clip Verification & Alignment",
            "detail": f"Verified {clip_count} clip segments with precise video timestamps and key quotes in {lang_name}.",
            "model": successful_model or requested_model,
            "message": f"Found {clip_count} viral clip candidates ({lang_name}) with {successful_model or requested_model} — reconstructing transcripts..."
        })
        logger.info(f"Gemini analysis complete with {successful_model or requested_model}. Found {clip_count} clips in {lang_name}.")

        # Reconstruct clip transcripts from enriched_transcript
        final_clips = []
        for raw_clip in analysis_data.get('clips', []):
            start = raw_clip.get('start_time', 0.0)
            end   = raw_clip.get('end_time', 0.0)
            hook  = raw_clip.get('hook_time')
            if hook is None or not (start <= hook <= end):
                hook = start
            
            # Enforce max 90 seconds (1m 30s) duration constraint for auto duration mode
            if is_auto_duration and (end - start) > 90.0:
                max_end = start + 90.0
                best_end = max_end
                for l in enriched_transcript:
                    l_end = l.get('end', 0.0)
                    if start + 15.0 <= l_end <= max_end:
                        best_end = l_end
                end = best_end

            # ── TikTok source: HARD-enforce 30s–60s clip window ──────────────
            if is_tiktok_source:
                # 1) Trim clips longer than 60s back to the nearest sentence end <= 60s (but >= 30s)
                if (end - start) > 60.0:
                    max_end = start + 60.0
                    best_end = max_end
                    for l in enriched_transcript:
                        l_end = l.get('end', 0.0)
                        if start + 30.0 <= l_end <= max_end:
                            best_end = l_end
                    end = best_end
                # 2) Extend clips shorter than 30s forward to the next sentence end reaching >= 30s
                if (end - start) < 30.0:
                    min_end = start + 30.0
                    best_end = min_end
                    for l in enriched_transcript:
                        l_end = l.get('end', 0.0)
                        if min_end <= l_end <= start + 60.0:
                            best_end = l_end
                            break
                    end = best_end
                # 3) Clamp to video bounds, then re-widen from the end if needed
                if end > duration:
                    end = duration
                if (end - start) < 30.0 and (end - 30.0) >= 0.0:
                    start = end - 30.0
                # 4) Final safety clamp so the window never exceeds 60s
                if (end - start) > 60.0:
                    end = start + 60.0
                if start < 0.0:
                    start = 0.0

            clip_lines = [
                line.get("text", "")
                for line in enriched_transcript
                if max(line.get("start", 0.0), start) < min(line.get("end", 0.0), end)
            ]
            
            # Ensure hashtags are always lowercase
            caption_sug = lowercase_hashtags_in_string(raw_clip.get('caption_suggestion', ''))
            hashtag_sug = lowercase_hashtags_in_string(raw_clip.get('hashtag_suggestion', ''))
            
            final_clips.append(ViralClip(
                title=sanitize_first_person_title(raw_clip.get('title', ''), channel, lang=lang_code),
                start_time=start,
                end_time=end,
                hook_time=hook,
                virality_score=raw_clip.get('virality_score', 0),
                key_quotes=raw_clip.get('key_quotes') or [],
                transcript=" ".join(clip_lines),
                title_suggestion=sanitize_first_person_title(raw_clip.get('title_suggestion', ''), channel, lang=lang_code),
                caption_suggestion=caption_sug,
                hashtag_suggestion=hashtag_sug
            ))

        # Enforce max constraint of ~200 clips for auto clip count
        if is_auto_clip_count and len(final_clips) > 200:
            final_clips = final_clips[:200]

        response_heatmap = [
            HeatmapPoint(
                start_time=float(pt.get('start_time', 0.0)),
                end_time=float(pt.get('end_time', 0.0)),
                value=float(pt.get('value', 0.0))
            )
            for pt in (heatmap or [])
        ]

        # ── Optional transcript translation ─────────────────────────────────
        # When the user asked for a transcript language different from the
        # video's own language, translate the transcript lines (timestamps kept).
        requested_transcript_lang = resolve_language_name(request.transcript_language)
        if requested_transcript_lang and requested_transcript_lang.lower() != lang_name.lower():
            yield _sse({
                "step": 4,
                "step_progress": 99,
                "overall_progress": 99,
                "stage": "Transcript Translation",
                "detail": f"Translating {len(enriched_transcript)} transcript lines into {requested_transcript_lang}...",
                "model": successful_model or requested_model,
                "message": f"Translating transcript into {requested_transcript_lang}..."
            })
            indexed_lines = [
                {
                    "i": idx,
                    "start": round(float(l["start"]), 2),
                    "end": round(float(l["end"]), 2),
                    "text": l.get("text", ""),
                }
                for idx, l in enumerate(enriched_transcript)
            ]
            try:
                parsed_translation = await asyncio.to_thread(
                    _translate_lines_sync,
                    indexed_lines, requested_transcript_lang, lang_code,
                    provider, openai_base_url, gemini_key,
                    (successful_model or requested_model),
                )
                translated_map = _extract_translated_map(parsed_translation)
                if translated_map:
                    for idx, l in enumerate(enriched_transcript):
                        if idx in translated_map and translated_map[idx]:
                            l["text"] = translated_map[idx]
                    logger.info(f"Transcript translated into {requested_transcript_lang} ({len(translated_map)} lines).")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Transcript translation into {requested_transcript_lang} failed: {exc}")
                yield _sse({
                    "step": 4,
                    "step_progress": 99,
                    "overall_progress": 99,
                    "stage": "Transcript Translation",
                    "detail": f"Transcript translation skipped ({exc}).",
                    "message": f"Transcript translation could not be completed: {exc}"
                })

        response_transcript = [
            TranscriptLine(
                start=float(line["start"]),
                end=float(line["end"]),
                text=line["text"],
                engagement=line.get("engagement")
            )
            for line in enriched_transcript
        ]

        # Ensure hashtags are lowercase in the overall summary
        clean_summary = lowercase_hashtags_in_string(analysis_data.get("summary", ""))

        final_result = AnalyzeResponse(
            video_id=video_id,
            title=title,
            channel=channel,
            duration=duration,
            heatmap=response_heatmap,
            summary=clean_summary,
            clips=final_clips,
            transcript=response_transcript,
            model=successful_model or requested_model,
            video_url=video_url,
            source_type=source_type
        )

        yield _sse({"done": True, "result": final_result.model_dump()})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection":    "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
