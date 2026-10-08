from typing import List, Optional
from pydantic import BaseModel


class ScheduleRequest(BaseModel):
    clip_count: int = 1
    platform: Optional[str] = "generic"
    clips_per_day: Optional[int] = None
    start_date: Optional[str] = None  # ISO date, default today
    lang: Optional[str] = "id"
    clip_titles: Optional[List[str]] = None


class ScheduleEntry(BaseModel):
    index: int
    date: str
    time: str
    datetime: str
    weekday: str
    day_number: int
    title: Optional[str] = None


class ScheduleRecommendation(BaseModel):
    platform: str
    platform_label: str
    platform_note: str
    clip_count: int
    recommended_per_day: int
    recommended_per_day_source: str
    safe_per_day: int
    max_per_day: int
    recommended_per_hour: int
    gap_hours: float
    ideal_gap_hours: List[float]
    best_hours: List[int]
    days_needed: int
    warnings: List[str]


class ScheduleResponse(BaseModel):
    recommendation: ScheduleRecommendation
    schedule: List[ScheduleEntry]
    start_date: str
    lang: str
