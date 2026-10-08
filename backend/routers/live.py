"""
Live-stream router: detect a live broadcast on paste and record it so the
recording can be clipped by the normal pipeline.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from backend.services.live_service import (
    get_live_status,
    live_record_jobs,
    probe_live_url,
    pump_recorder_output,
    start_live_record,
    stop_live_record,
)
from backend.services.live_autoclip_service import (
    get_live_autoclip_status,
    live_autoclip_jobs,
    start_live_autoclip,
    stop_live_autoclip,
)

router = APIRouter(tags=["Live"])


class LiveProbeRequest(BaseModel):
    url: str


class LiveRecordRequest(BaseModel):
    url: str
    from_start: bool = False
    title: Optional[str] = None


class LiveAutoClipRequest(BaseModel):
    url: str
    interval_minutes: int = 60
    clip_seconds: int = 60
    title: Optional[str] = None
    back_offset_seconds: float = 0.0


@router.post("/api/live/probe")
async def api_live_probe(req: LiveProbeRequest):
    """Checks whether a pasted link is a live broadcast (yt-dlp is_live/live_status)."""
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    result = await asyncio.to_thread(probe_live_url, req.url.strip())
    if result.get("error"):
        # Not fatal: the frontend simply shows a soft warning and keeps normal flow.
        return {"ok": False, "error": result["error"]}
    return {"ok": True, **result}


@router.post("/api/live/record/start")
async def api_live_record_start(req: LiveRecordRequest):
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    try:
        info = start_live_record(req.url.strip(), from_start=req.from_start, title=req.title)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {e}")

    job_id = info["job_id"]
    # Drain the recorder's output in the background for the lifetime of the process.
    asyncio.create_task(pump_recorder_output(job_id))
    return info


@router.get("/api/live/record/status/{job_id}")
async def api_live_record_status(job_id: str):
    try:
        return get_live_status(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Recording job not found")


@router.post("/api/live/record/stop/{job_id}")
async def api_live_record_stop(job_id: str):
    try:
        return stop_live_record(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Recording job not found")


@router.get("/api/live/record/jobs")
async def api_live_record_jobs():
    return {"jobs": list(live_record_jobs.values())}


# --------------------------------------------------------------------------- #
#  Auto-clip: grab the most recent N seconds from the live edge every interval
# --------------------------------------------------------------------------- #
@router.post("/api/live/autoclip/start")
async def api_live_autoclip_start(req: LiveAutoClipRequest):
    """Starts an auto-clip job: every `interval_minutes` it saves the latest
    `clip_seconds` from the live edge as a local video."""
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    try:
        info = start_live_autoclip(
            req.url.strip(),
            interval_minutes=req.interval_minutes,
            clip_seconds=req.clip_seconds,
            title=req.title,
            back_offset_seconds=req.back_offset_seconds,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to start auto-clip: {e}")
    return info


@router.get("/api/live/autoclip/status/{job_id}")
async def api_live_autoclip_status(job_id: str):
    try:
        return get_live_autoclip_status(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Auto-clip job not found")


@router.post("/api/live/autoclip/stop/{job_id}")
async def api_live_autoclip_stop(job_id: str):
    try:
        return stop_live_autoclip(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Auto-clip job not found")


@router.get("/api/live/autoclip/jobs")
async def api_live_autoclip_jobs():
    return {"jobs": [{k: v for k, v in j.items() if k != "token"} for j in live_autoclip_jobs.values()]}


@router.get("/api/live/autoclip/download/{job_id}/{index}")
async def api_live_autoclip_download(job_id: str, index: int):
    """Downloads a captured auto-clip as a normal mp4 attachment."""
    job = live_autoclip_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Auto-clip job not found")
    match = next((c for c in job.get("clips", []) if c.get("index") == index), None)
    if not match:
        raise HTTPException(status_code=404, detail="Clip not found")
    path = Path(match.get("file_path", ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip file missing on disk")
    safe_title = "".join(ch for ch in (job.get("title") or "clip") if ch not in '\\/*?:"<>|').strip() or "clip"
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"{safe_title}_clip{index}.mp4",
    )
