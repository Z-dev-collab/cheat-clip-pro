"""Persistent render history for finished clip render batches.

Stores a compact JSON record of every completed/failed render batch so the
Studio can show a "Render History" list across sessions. The store lives in
EXPORTS_DIR (runtime data, git-ignored) and is capped to the newest entries.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import EXPORTS_DIR, logger

HISTORY_FILE: Path = EXPORTS_DIR / "render_history.json"
MAX_HISTORY_ENTRIES = 200


def _read_history() -> List[Dict[str, Any]]:
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.warning(f"Failed to read render history: {e}")
    return []


def _write_history(entries: List[Dict[str, Any]]) -> None:
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_name(HISTORY_FILE.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries[:MAX_HISTORY_ENTRIES], f, ensure_ascii=False, indent=2)
        tmp.replace(HISTORY_FILE)
    except Exception as e:
        logger.warning(f"Failed to write render history: {e}")


def _clip_record(c: Dict[str, Any]) -> Dict[str, Any]:
    start = c.get("start_time")
    end = c.get("end_time")
    duration = None
    try:
        if start is not None and end is not None:
            duration = round(float(end) - float(start), 2)
    except Exception:
        duration = None
    return {
        "clip_index": c.get("clip_index"),
        "title": c.get("title"),
        "base_title": c.get("base_title"),
        "status": c.get("status"),
        "download_url": c.get("download_url"),
        "cover_url": c.get("cover_url"),
        "start_time": start,
        "end_time": end,
        "duration": duration,
    }


def _settings_summary(settings: Optional[Any]) -> Optional[Dict[str, Any]]:
    if settings is None:
        return None
    return {
        "aspect_ratio": getattr(settings, "aspect_ratio", None),
        "caption_style": getattr(settings, "caption_style", None),
        "render_mode": getattr(settings, "render_mode", "separate"),
        "cover_enabled": bool(getattr(settings, "cover_enabled", False)),
    }


def save_render_history(batch_id: str, batch: Dict[str, Any], settings: Optional[Any] = None) -> None:
    """Upsert a render batch into the persistent history (newest first)."""
    if not batch:
        return
    clips = batch.get("clips", []) or []
    completed = [c for c in clips if c.get("status") == "completed"]
    failed = [c for c in clips if c.get("status") == "error"]

    entry: Dict[str, Any] = {
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": batch.get("overall_status"),
        "is_merged": bool(batch.get("is_merged")),
        "total_clips": batch.get("total_clips", len(clips)),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "zip_url": batch.get("zip_url"),
        "video_id": batch.get("video_id"),
        "video_url": batch.get("video_url"),
        "settings": _settings_summary(settings),
        "clips": [_clip_record(c) for c in clips],
    }

    entries = [e for e in _read_history() if e.get("batch_id") != batch_id]
    entries.insert(0, entry)
    _write_history(entries)
    logger.info(f"Render history saved for batch {batch_id} ({len(completed)} completed)")


def list_render_history() -> List[Dict[str, Any]]:
    return _read_history()


def get_render_history_entry(batch_id: str) -> Optional[Dict[str, Any]]:
    for e in _read_history():
        if e.get("batch_id") == batch_id:
            return e
    return None


def delete_render_history_entry(batch_id: str) -> bool:
    entries = _read_history()
    remaining = [e for e in entries if e.get("batch_id") != batch_id]
    if len(remaining) == len(entries):
        return False
    _write_history(remaining)
    return True


def clear_render_history() -> int:
    entries = _read_history()
    _write_history([])
    return len(entries)
