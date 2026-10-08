"""
Upload / air-time (jam tayang) scheduler.

Computes a safe posting cadence and a concrete publish timetable for a set of
clips, based on widely-recommended anti-spam limits:

  * Never post more than 1 video per hour (hard rule).
  * Keep 2-4 hours between posts; avoid "bunching" many uploads together
    (a common trigger for shadowbans / reduced reach).
  * Respect per-day caps per platform.

The planner is deterministic and offline: it needs no API keys.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Platform presets
# ---------------------------------------------------------------------------
# `safe_per_day`   -> comfortable, low-risk daily volume
# `max_per_day`    -> absolute ceiling before reach/limits degrade
# `gap_hours`      -> recommended minimum spacing between two posts
# `best_hours`     -> local-time posting windows with strongest engagement
PLATFORM_PRESETS: Dict[str, Dict[str, Any]] = {
    "youtube_long": {
        "label": "YouTube (video panjang)",
        "safe_per_day": 2,
        "max_per_day": 5,
        "gap_hours": 3.0,
        "best_hours": [12, 17, 20],
        "note": "Video panjang: 1-2/hari paling aman, 3-5/hari masih wajar, >5 mulai berisiko.",
    },
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "safe_per_day": 3,
        "max_per_day": 5,
        "gap_hours": 2.0,
        "best_hours": [7, 12, 17, 20],
        "note": "Shorts: 1-3/hari paling aman, 5/hari wajar, >10/hari berisiko.",
    },
    "tiktok": {
        "label": "TikTok",
        "safe_per_day": 4,
        "max_per_day": 10,
        "gap_hours": 2.0,
        "best_hours": [7, 12, 17, 21],
        "note": "TikTok: 1-4/hari paling aman (via API maks ~15/hari, 6 request/menit).",
    },
    "mixed": {
        "label": "Multi-platform (YouTube + TikTok)",
        "safe_per_day": 3,
        "max_per_day": 6,
        "gap_hours": 3.0,
        "best_hours": [7, 12, 17, 20],
        "note": "Lintas platform: sebar merata, jangan unggah serentak ke semua kanal sekaligus.",
    },
    "generic": {
        "label": "Umum / platform lain",
        "safe_per_day": 3,
        "max_per_day": 5,
        "gap_hours": 3.0,
        "best_hours": [7, 12, 17, 20],
        "note": "Aturan umum: maksimal 1 unggahan/jam dan jaga jarak 2-4 jam antar unggahan.",
    },
}

# Hard global rule, independent of platform.
HARD_MAX_PER_HOUR = 1
IDEAL_GAP_HOURS = (2.0, 4.0)

_WEEKDAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _weekday_name(weekday: int, lang: str) -> str:
    names = _WEEKDAYS_ID if str(lang).lower().startswith("id") else _WEEKDAYS_EN
    return names[weekday % 7]


def get_platform_preset(platform: Optional[str]) -> Dict[str, Any]:
    key = (platform or "generic").strip().lower()
    if key not in PLATFORM_PRESETS:
        key = "generic"
    preset = dict(PLATFORM_PRESETS[key])
    preset["key"] = key
    return preset


def compute_recommendation(
    clip_count: int,
    platform: Optional[str] = None,
    clips_per_day: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Returns the recommended posting cadence for `clip_count` clips.

    If `clips_per_day` is not supplied it defaults to the platform's safe daily
    volume. The returned `recommended_per_hour` is always 1 (the hard rule).
    """
    preset = get_platform_preset(platform)
    count = max(0, int(clip_count or 0))

    safe = int(preset["safe_per_day"])
    hard_cap = int(preset["max_per_day"])
    gap = float(preset["gap_hours"])

    if clips_per_day is None or int(clips_per_day) <= 0:
        per_day = safe
        per_day_source = "safe_default"
    else:
        per_day = int(clips_per_day)
        per_day_source = "user"

    # Clamp the user's request into the platform's ceiling.
    clamped = False
    if per_day > hard_cap:
        per_day = hard_cap
        clamped = True
    if per_day < 1:
        per_day = 1

    days_needed = (count + per_day - 1) // per_day if count else 0

    warnings: List[str] = []
    if clamped:
        warnings.append(
            f"Jumlah per hari dibatasi ke {hard_cap} (batas aman {preset['label']}). "
            f"Lebih dari itu berisiko shadowban / reach turun."
        )
    if per_day > safe:
        warnings.append(
            f"{per_day}/hari sudah di atas volume nyaman ({safe}/hari) untuk {preset['label']}. "
            f"Masih boleh, tapi pantau performa & jangan borongan."
        )
    if gap < IDEAL_GAP_HOURS[0]:
        warnings.append(
            f"Jarak antar unggahan disarankan {IDEAL_GAP_HOURS[0]:.0f}-{IDEAL_GAP_HOURS[1]:.0f} jam; "
            f"jangan lebih dari {HARD_MAX_PER_HOUR} unggahan/jam."
        )

    return {
        "platform": preset["key"],
        "platform_label": preset["label"],
        "platform_note": preset["note"],
        "clip_count": count,
        "recommended_per_day": per_day,
        "recommended_per_day_source": per_day_source,
        "safe_per_day": safe,
        "max_per_day": hard_cap,
        "recommended_per_hour": HARD_MAX_PER_HOUR,
        "gap_hours": gap,
        "ideal_gap_hours": list(IDEAL_GAP_HOURS),
        "best_hours": list(preset["best_hours"]),
        "days_needed": days_needed,
        "warnings": warnings,
    }


def _pick_daily_hours(count_today: int, best_hours: List[int], per_day: int) -> List[float]:
    """
    Chooses `count_today` posting times (as float hours) for one day, spread
    across the platform's best hours and never closer than the hard 1/hour rule.
    """
    hours = sorted(best_hours) or [7, 12, 17, 20]
    n = max(1, min(count_today, len(hours) if count_today <= len(hours) else count_today))

    if count_today <= len(hours):
        # Evenly sample the available windows.
        if count_today == 1:
            picks = [hours[len(hours) // 2]]
        else:
            step = (len(hours) - 1) / (count_today - 1)
            picks = [hours[round(i * step)] for i in range(count_today)]
    else:
        # More clips than windows: start from the windows and add evenly spaced
        # extra slots at least 1 hour apart, staying inside 06:00-23:00.
        picks = list(hours)
        extra = count_today - len(hours)
        slot = 6.0
        for _ in range(extra):
            slot += 1.0
            if slot > 23.0:
                slot = 6.0
            picks.append(slot)
        picks = sorted(set(picks))
        while len(picks) < count_today:
            picks.append(min(23.0, picks[-1] + 1.0))

    picks = sorted(picks)
    # Enforce the hard 1-per-hour rule.
    deduped: List[float] = []
    for h in picks:
        if not deduped or (h - deduped[-1]) >= 1.0:
            deduped.append(h)
    return deduped[:count_today]


def build_schedule(
    clip_count: int,
    platform: Optional[str] = None,
    clips_per_day: Optional[int] = None,
    start_date: Optional[str] = None,
    lang: str = "id",
    clip_titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Builds a concrete publish timetable (date + time per clip) plus the
    frequency recommendation. Times are local wall-clock times.
    """
    rec = compute_recommendation(clip_count, platform, clips_per_day)
    count = rec["clip_count"]
    per_day = rec["recommended_per_day"]
    best_hours = rec["best_hours"]

    # Resolve the start date (default: today).
    if start_date:
        try:
            day = _dt.date.fromisoformat(start_date)
        except Exception:
            day = _dt.date.today()
    else:
        day = _dt.date.today()

    entries: List[Dict[str, Any]] = []
    remaining = count
    day_offset = 0
    idx = 0

    while remaining > 0 and day_offset < 366:
        current_day = day + _dt.timedelta(days=day_offset)
        today_count = min(per_day, remaining)
        times = _pick_daily_hours(today_count, best_hours, per_day)

        for h in times:
            if remaining <= 0:
                break
            hour = int(h)
            minute = int(round((h - hour) * 60))
            if minute >= 60:
                hour += 1
                minute -= 60
            dt = _dt.datetime(current_day.year, current_day.month, current_day.day, min(hour, 23), minute)
            title = None
            if clip_titles and idx < len(clip_titles):
                title = clip_titles[idx]
            entries.append({
                "index": idx + 1,
                "date": dt.date().isoformat(),
                "time": dt.strftime("%H:%M"),
                "datetime": dt.isoformat(timespec="minutes"),
                "weekday": _weekday_name(dt.weekday(), lang),
                "day_number": day_offset + 1,
                "title": title,
            })
            idx += 1
            remaining -= 1

        day_offset += 1

    return {
        "recommendation": rec,
        "schedule": entries,
        "start_date": day.isoformat(),
        "lang": lang,
    }
