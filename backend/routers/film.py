"""
Archive.org film router for Cheat Clip Pro  (legal, public-domain source).

Endpoints (all prefixed /api/film):
    POST /api/film/search                       -> cari film di Archive.org
    GET  /api/film/parts/{identifier}           -> daftar part (file video) + metadata
    GET  /api/film/preview/{identifier}/{file}  -> proxy Range untuk pemutaran/preview
    POST /api/film/download                     -> unduh part terpilih (SSE progress)
                                                   lalu serahkan ke pipeline /api/analyze

The download endpoint streams Server-Sent Events so the web UI can show live
progress, exactly like /api/analyze. On completion it emits
{ done: true, result: { video_id, saved_name, video_url, title, source_type } }
so the frontend can immediately run the normal analyze/preview flow.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from backend.config import logger
from backend.schemas.film import (
    BgmDownloadRequest,
    BgmRecommendRequest,
    BgmRecommendResponse,
    FilmDownloadRequest,
    FilmPartsResponse,
    FilmSearchRequest,
    FilmSearchResponse,
    FilmSegmentsRequest,
    FilmSegmentsResponse,
)
from backend.services.film_service import (
    USER_AGENT,
    _download_url,
    download_archive_audio,
    download_archive_file,
    get_item_parts,
    plan_film_segments,
    recommend_bgm,
    search_archive,
)

router = APIRouter()

_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".ogv": "video/ogg",
    ".avi": "video/x-msvideo",
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/api/film/search", response_model=FilmSearchResponse)
async def film_search(request: FilmSearchRequest):
    """Cari film domain-publik di Archive.org berdasarkan judul."""
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Judul film tidak boleh kosong.")
    try:
        results = await asyncio.to_thread(search_archive, query, max(1, min(40, request.limit)))
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("film_search error")
        raise HTTPException(status_code=502, detail=f"Pencarian film gagal: {exc}")
    return FilmSearchResponse(query=query, count=len(results), results=results)


@router.get("/api/film/parts/{identifier}", response_model=FilmPartsResponse)
async def film_parts(identifier: str):
    """Daftar part (file video) dari sebuah item Archive.org."""
    try:
        data = await asyncio.to_thread(get_item_parts, identifier)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("film_parts error")
        raise HTTPException(status_code=502, detail=f"Gagal mengambil part film: {exc}")
    return FilmPartsResponse(**data)


@router.post("/api/film/segments", response_model=FilmSegmentsResponse)
async def film_segments(request: FilmSegmentsRequest):
    """Pecah film menjadi trailer + part berurutan (default 60s) hingga durasi tamat.

    Bila `video_url` (file lokal di server) disertakan, potongan trailer dipilih
    dari jendela dengan energi audio tertinggi (adegan paling seru).
    """
    duration = float(request.duration or 0.0)

    # Best-effort: enrich with a local audio-energy heatmap so the trailer is
    # chosen from the most engaging window instead of always starting at 0.
    heatmap = None
    video_url = (request.video_url or "").strip()
    if video_url and duration > 0:
        try:
            from backend.video_engine import (
                compute_audio_energy_heatmap,
                get_video_file_metadata,
            )
            from backend.config import UPLOADS_DIR, TEMP_DIR, EXPORTS_DIR
            import os as _os

            base = _os.path.basename(video_url.split("?")[0])
            local = None
            for d in (UPLOADS_DIR, TEMP_DIR, EXPORTS_DIR):
                cand = d / base
                if cand.exists():
                    local = cand
                    break
            if local is not None:
                meta = await asyncio.to_thread(get_video_file_metadata, local)
                real_dur = float(meta.get("duration") or 0.0)
                if real_dur > 0:
                    duration = real_dur
                heatmap = await asyncio.to_thread(
                    compute_audio_energy_heatmap, local, duration, 100
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"film_segments heatmap skipped: {exc}")
            heatmap = None

    try:
        plan = await asyncio.to_thread(
            plan_film_segments,
            duration,
            float(request.part_seconds or 60.0),
            float(request.trailer_seconds or 60.0),
            bool(request.include_trailer),
            heatmap,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("film_segments error")
        raise HTTPException(status_code=400, detail=f"Gagal memecah film: {exc}")

    return FilmSegmentsResponse(**plan)


@router.post("/api/film/bgm-recommend", response_model=BgmRecommendResponse)
async def film_bgm_recommend(request: BgmRecommendRequest):
    """Rekomendasi latar musik legal (Archive.org) berdasarkan mood/suasana."""
    try:
        data = await asyncio.to_thread(
            recommend_bgm,
            request.mood or "epic",
            request.query or "",
            max(1, min(12, int(request.limit or 6))),
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("film_bgm_recommend error")
        raise HTTPException(status_code=502, detail=f"Gagal mengambil rekomendasi musik: {exc}")
    return BgmRecommendResponse(**data)


@router.post("/api/film/bgm-download")
async def film_bgm_download(request: BgmDownloadRequest):
    """Unduh track musik terpilih (SSE progress); selesai -> path + URL audio lokal."""
    if not (request.identifier or "").strip() or not (request.file or "").strip():
        raise HTTPException(status_code=400, detail="Identifier / file audio kosong.")

    async def stream():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def progress(stage: str, detail: str, step_pct: int = 30):
            loop.call_soon_threadsafe(q.put_nowait, {
                "step": 1,
                "step_progress": step_pct,
                "overall_progress": min(95, max(2, step_pct)),
                "stage": stage,
                "detail": detail,
                "message": detail,
            })

        yield _sse({
            "step": 1,
            "step_progress": 2,
            "overall_progress": 2,
            "stage": "Memulai",
            "detail": "Menyiapkan unduhan musik dari Archive.org...",
            "message": "Menyiapkan unduhan musik dari Archive.org...",
        })

        try:
            task = asyncio.create_task(asyncio.to_thread(
                download_archive_audio,
                request.identifier,
                request.file,
                request.title_hint,
                progress,
            ))
            while not task.done():
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=0.25)
                    yield _sse(evt)
                except asyncio.TimeoutError:
                    pass
            while not q.empty():
                yield _sse(q.get_nowait())
            path: Path = await task
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"film_bgm_download failed: {exc}")
            yield _sse({"error": f"Gagal mengunduh musik: {exc}", "status": 400})
            return

        result = {
            "saved_name": path.name,
            "file_path": str(path),
            "audio_url": f"/api/audio/{quote(path.name)}",
            "title": request.title_hint or path.stem,
            "size_bytes": path.stat().st_size,
        }
        yield _sse({
            "step": 1,
            "step_progress": 100,
            "overall_progress": 100,
            "stage": "Unduhan Selesai",
            "detail": f"Musik siap dipakai: {path.name}",
            "message": f"Musik siap dipakai: {path.name}",
            "done": True,
            "result": result,
        })

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/film/preview/{identifier}/{file_name:path}")
async def film_preview(identifier: str, file_name: str, request: Request):
    """Stream (proxy) a chosen Archive.org part with HTTP Range support.

    Lets the browser <video> element preview the exact segment before the user
    commits to a full download. Nothing is stored locally here.
    """
    if not identifier or not file_name:
        raise HTTPException(status_code=400, detail="Identifier / nama file kosong.")

    upstream = _download_url(identifier, file_name)
    ext = Path(file_name).suffix.lower()
    media_type = _MEDIA_TYPES.get(ext, "video/mp4")

    range_header = request.headers.get("range")
    headers = {"User-Agent": USER_AGENT}
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0), follow_redirects=True)
    try:
        req = client.build_request("GET", upstream, headers=headers)
        upstream_resp = await client.send(req, stream=True)
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Gagal membuka stream Archive.org: {exc}")

    status = upstream_resp.status_code
    if status >= 400:
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Archive.org menolak stream (HTTP {status}).")

    passthrough = {}
    for key, header in (
        ("content-range", "Content-Range"),
        ("content-length", "Content-Length"),
        ("accept-ranges", "Accept-Ranges"),
    ):
        value = upstream_resp.headers.get(key)
        if value:
            passthrough[header] = value
    passthrough.setdefault("Accept-Ranges", "bytes")
    passthrough["Cache-Control"] = "public, max-age=3600"

    async def body():
        try:
            async for chunk in upstream_resp.aiter_bytes(1024 * 256):
                if chunk:
                    yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=passthrough)


@router.post("/api/film/download")
async def film_download(request: FilmDownloadRequest):
    """Unduh part terpilih dengan progress SSE; selesai -> metadata file lokal."""
    if not (request.identifier or "").strip() or not (request.file or "").strip():
        raise HTTPException(status_code=400, detail="Identifier / file part kosong.")

    async def stream():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def progress(stage: str, detail: str, step_pct: int = 30):
            loop.call_soon_threadsafe(q.put_nowait, {
                "step": 1,
                "step_progress": step_pct,
                "overall_progress": min(95, max(2, step_pct)),
                "stage": stage,
                "detail": detail,
                "message": detail,
            })

        yield _sse({
            "step": 1,
            "step_progress": 2,
            "overall_progress": 2,
            "stage": "Memulai",
            "detail": "Menyiapkan unduhan part dari Archive.org...",
            "message": "Menyiapkan unduhan part dari Archive.org...",
        })

        try:
            task = asyncio.create_task(asyncio.to_thread(
                download_archive_file,
                request.identifier,
                request.file,
                request.title_hint,
                progress,
            ))
            while not task.done():
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=0.25)
                    yield _sse(evt)
                except asyncio.TimeoutError:
                    pass
            while not q.empty():
                yield _sse(q.get_nowait())

            path: Path = await task
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"film_download failed: {exc}")
            yield _sse({"error": f"Gagal mengunduh part film: {exc}", "status": 400})
            return

        # Hand the local file to the existing analyze pipeline.
        try:
            from backend.video_engine import get_video_file_metadata
            meta = await asyncio.to_thread(get_video_file_metadata, path)
        except Exception:  # noqa: BLE001
            meta = {}

        video_id = path.stem
        result = {
            "video_id": video_id,
            "saved_name": path.name,
            "video_url": f"/api/video/{quote(path.name)}",
            "title": request.title_hint or path.stem,
            "duration": float(meta.get("duration") or 0.0),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "source_type": "upload",
            "size_bytes": path.stat().st_size,
        }
        yield _sse({
            "step": 1,
            "step_progress": 100,
            "overall_progress": 100,
            "stage": "Unduhan Selesai",
            "detail": f"Video siap dianalisis: {path.name}",
            "message": f"Video siap dianalisis: {path.name}",
            "done": True,
            "result": result,
        })

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
