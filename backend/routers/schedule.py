import logging

from fastapi import APIRouter, Query

from backend.schemas.schedule import ScheduleRequest, ScheduleResponse
from backend.services.schedule_service import (
    PLATFORM_PRESETS,
    build_schedule,
    compute_recommendation,
)

router = APIRouter(tags=["Schedule"])
logger = logging.getLogger("cheat-clip-pro.schedule")


@router.get("/api/schedule/platforms")
async def list_schedule_platforms():
    """Lists supported platforms and their safe posting limits."""
    platforms = []
    for key, preset in PLATFORM_PRESETS.items():
        platforms.append({
            "key": key,
            "label": preset["label"],
            "safe_per_day": preset["safe_per_day"],
            "max_per_day": preset["max_per_day"],
            "gap_hours": preset["gap_hours"],
            "best_hours": preset["best_hours"],
            "note": preset["note"],
        })
    return {"platforms": platforms}


@router.get("/api/schedule/recommend")
async def recommend_schedule(
    clip_count: int = Query(1, ge=0),
    platform: str = Query("generic"),
    clips_per_day: int = Query(None),
    lang: str = Query("id"),
):
    """Returns just the frequency recommendation (how many per hour/day)."""
    rec = compute_recommendation(clip_count, platform, clips_per_day)
    rec["lang"] = lang
    return rec


@router.post("/api/schedule/plan", response_model=ScheduleResponse)
async def plan_schedule(request: ScheduleRequest):
    """
    Builds a full publish timetable for the given clips, respecting the
    anti-spam cadence (max 1/hour, 2-4h gap, per-platform daily caps).
    """
    logger.info(
        f"Schedule plan requested: {request.clip_count} clips, "
        f"platform={request.platform}, per_day={request.clips_per_day}"
    )
    return build_schedule(
        clip_count=request.clip_count,
        platform=request.platform,
        clips_per_day=request.clips_per_day,
        start_date=request.start_date,
        lang=request.lang or "id",
        clip_titles=request.clip_titles,
    )
