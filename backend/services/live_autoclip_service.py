"""
Live auto-clip service.

While a live broadcast is running, automatically grab a short clip straight
from the LIVE EDGE every N minutes (default: every 60 minutes, 60 seconds
long). Because each grab is cut from the live edge, only the most recent part
of the stream is ever captured — nothing older than the configured clip length
is kept, which is exactly the "only ~1 hour / most recent minute" behaviour.

Every grab is saved as an ordinary local video inside UPLOADS_DIR so the whole
existing pipeline (AI analysis, Clip Studio, subtitles, rendering) can process
it unchanged — the user just gets a ready-made list of clips to download or
send to the studio.
"""

import asyncio
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import UPLOADS_DIR, is_valid_mp4, logger

# --------------------------------------------------------------------------- #
#  Job registry (in-memory, same style as live_service / render_service)
# --------------------------------------------------------------------------- #
live_autoclip_jobs: Dict[str, dict] = {}
_autoclip_tasks: Dict[str, asyncio.Task] = {}

AUTOCLIP_PREFIX = "autoclip_"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DEFAULT_INTERVAL_MINUTES = 60
DEFAULT_CLIP_SECONDS = 60


def _safe_name(text: str, fallback: str = "live") -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", (text or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:70] or fallback


# --------------------------------------------------------------------------- #
#  Stream helpers
# --------------------------------------------------------------------------- #
def _resolve_live_stream_urls(url: str, timeout: int = 60) -> List[str]:
    """Resolve the playable (video[,audio]) URLs of a live broadcast via yt-dlp -g."""
    try:
        from backend.video_engine import get_yt_dlp_base_cmd
        base = list(get_yt_dlp_base_cmd(include_cookies=True))
    except Exception:  # noqa: BLE001
        base = [sys.executable, "-m", "yt_dlp"]

    cmd = base + ["-g", "-f", "b/bv*+ba", "--no-warnings", url]
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if res.returncode != 0:
        raise RuntimeError(
            (res.stderr or "yt-dlp could not resolve the live stream URL").strip()[:300]
        )
    urls = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip().startswith("http")]
    if not urls:
        raise RuntimeError("No playable stream URL found for this live broadcast.")
    return urls


def _grab_live_clip(stream_urls: List[str], out_path: Path, clip_seconds: int) -> None:
    """Record `clip_seconds` from the live edge into out_path (stream copy)."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-rw_timeout", "15000000",           # abort if the socket stalls 15s
    ]
    for u in stream_urls:
        cmd += ["-i", u]
    cmd += ["-t", str(int(clip_seconds))]
    if len(stream_urls) >= 2:
        # Separate video + audio streams -> map explicitly.
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += [
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=int(clip_seconds) + 150,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Live clip capture timed out.")
    if not out_path.exists() or not is_valid_mp4(out_path):
        raise RuntimeError(
            (res.stderr or "Live clip capture produced no usable file").strip()[:300]
        )


# --------------------------------------------------------------------------- #
#  Start / stop
# --------------------------------------------------------------------------- #
def start_live_autoclip(
    url: str,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    clip_seconds: int = DEFAULT_CLIP_SECONDS,
    title: Optional[str] = None,
) -> dict:
    """Registers a job and launches its background capture loop. Returns {job_id,...}."""
    clean = (url or "").strip()
    if not clean:
        raise ValueError("URL is required")

    interval_minutes = max(1, int(interval_minutes or DEFAULT_INTERVAL_MINUTES))
    clip_seconds = max(5, min(600, int(clip_seconds or DEFAULT_CLIP_SECONDS)))

    job_id = uuid.uuid4().hex[:8]
    token = uuid.uuid4().hex[:6]

    live_autoclip_jobs[job_id] = {
        "job_id": job_id,
        "token": token,
        "status": "starting",          # starting | capturing | waiting | completed | stopped | failed
        "url": clean,
        "title": _safe_name(title or "Live Stream"),
        "interval_seconds": interval_minutes * 60,
        "interval_minutes": interval_minutes,
        "clip_seconds": clip_seconds,
        "started_at": time.time(),
        "next_clip_at": None,
        "clips": [],                   # [{index, filename, file_path, video_url, download_url, size_bytes, duration, created_at}]
        "error": None,
        "stop_requested": False,
    }

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        _autoclip_tasks[job_id] = loop.create_task(_autoclip_loop(job_id))
    return {
        "job_id": job_id,
        "status": "starting",
        "interval_minutes": interval_minutes,
        "clip_seconds": clip_seconds,
    }


def stop_live_autoclip(job_id: str) -> dict:
    job = live_autoclip_jobs.get(job_id)
    if not job:
        raise KeyError("job not found")
    job["stop_requested"] = True
    job["status"] = "stopped"
    task = _autoclip_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    return {"job_id": job_id, "status": "stopped"}


def get_live_autoclip_status(job_id: str) -> dict:
    job = live_autoclip_jobs.get(job_id)
    if not job:
        raise KeyError("job not found")
    out = dict(job)
    if job.get("next_clip_at"):
        out["seconds_to_next"] = max(0, int(job["next_clip_at"] - time.time()))
    out.pop("token", None)
    return out


def stop_all_autoclips():
    for job_id in list(_autoclip_tasks.keys()):
        try:
            stop_live_autoclip(job_id)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
#  Capture loop
# --------------------------------------------------------------------------- #
async def _autoclip_loop(job_id: str):
    job = live_autoclip_jobs.get(job_id)
    if not job:
        return

    interval = int(job["interval_seconds"])
    clip_seconds = int(job["clip_seconds"])
    url = job["url"]
    token = job["token"]

    try:
        first = True
        while not job.get("stop_requested"):
            if not first:
                # Sleep until the next capture slot, waking every second so a
                # stop request is honoured promptly.
                next_at = time.time() + interval
                job["next_clip_at"] = next_at
                job["status"] = "waiting"
                while time.time() < next_at and not job.get("stop_requested"):
                    await asyncio.sleep(1)
                if job.get("stop_requested"):
                    break
            first = False

            job["status"] = "capturing"
            job["next_clip_at"] = None
            index = len(job["clips"]) + 1
            out_name = f"{AUTOCLIP_PREFIX}{token}_{index}.mp4"
            out_path = UPLOADS_DIR / out_name

            try:
                stream_urls = await asyncio.to_thread(_resolve_live_stream_urls, url)
                await asyncio.to_thread(_grab_live_clip, stream_urls, out_path, clip_seconds)
                size = out_path.stat().st_size
                job["clips"].append({
                    "index": index,
                    "filename": out_name,
                    "file_path": str(out_path),
                    "video_url": f"/api/video/{out_name}",
                    "download_url": f"/api/live/autoclip/download/{job_id}/{index}",
                    "size_bytes": size,
                    "duration": clip_seconds,
                    "created_at": time.time(),
                })
                job["error"] = None
                logger.info(f"[autoclip {job_id}] captured clip #{index}: {out_name} ({size/1048576:.1f} MB)")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[autoclip {job_id}] clip #{index} failed: {e}")
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except Exception:
                        pass
                # Is the broadcast still live? If not, finish the job cleanly.
                still_live = False
                try:
                    from backend.services.live_service import probe_live_url
                    info = await asyncio.to_thread(probe_live_url, url)
                    still_live = bool(info.get("is_live")) or info.get("live_status") == "is_live"
                except Exception:  # noqa: BLE001
                    still_live = True
                if not still_live:
                    job["status"] = "completed"
                    job["ended_reason"] = "Broadcast ended"
                    logger.info(f"[autoclip {job_id}] broadcast ended; job complete with {len(job['clips'])} clip(s).")
                    return
                job["error"] = str(e)

        job["status"] = "stopped" if job.get("stop_requested") else "completed"
    except asyncio.CancelledError:
        job["status"] = "stopped"
        raise
    except Exception as e:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(e)
        logger.error(f"[autoclip {job_id}] loop failed: {e}")
    finally:
        _autoclip_tasks.pop(job_id, None)
