"""
Live-stream router: detect a live broadcast on paste and record it so the
recording can be clipped by the normal pipeline.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.services.live_service import (
    get_live_status,
    live_record_jobs,
    probe_live_url,
    pump_recorder_output,
    start_live_record,
    stop_live_record,
)

router = APIRouter(tags=["Live"])


class LiveProbeRequest(BaseModel):
    url: str


class LiveRecordRequest(BaseModel):
    url: str
    from_start: bool = False
    title: Optional[str] = None


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
