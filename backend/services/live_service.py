"""
Live-stream support for Cheat Clip PRO.

Adds what the original repo was missing: the ability to *detect* a live
broadcast the moment its link is pasted, and to *record* it so that the
recording becomes an ordinary local video that the whole existing pipeline
(AI analysis, Clip Studio, subtitles, rendering) can clip normally.

Detection follows the reference project's approach: yt-dlp's ``is_live`` /
``live_status`` fields.
"""

import asyncio
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import yt_dlp

from backend.config import UPLOADS_DIR, get_effective_cookies_path, logger

# --------------------------------------------------------------------------- #
#  Job registry (in-memory, same style as the download/render services)
# --------------------------------------------------------------------------- #
live_record_jobs: Dict[str, dict] = {}
_live_procs: Dict[str, subprocess.Popen] = {}

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

# Live recordings land in UPLOADS_DIR so the existing upload pipeline
# (transcription, analysis, clip studio, rendering) picks them up unchanged.
LIVE_PREFIX = "upload_live_"


# --------------------------------------------------------------------------- #
#  Probe: is this link a live broadcast right now?
# --------------------------------------------------------------------------- #
def probe_live_url(url: str, timeout: int = 25) -> dict:
    """Returns {title, channel, duration, is_live, live_status, thumbnail, url}."""
    clean = (url or "").strip()
    if not clean:
        return {"error": "Empty URL"}

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "socket_timeout": timeout,
        "extractor_args": {"youtube": {"player_client": ["default", "web_embedded", "ios"]}},
    }
    cookies = get_effective_cookies_path()
    if cookies:
        ydl_opts["cookiefile"] = str(cookies)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean, download=False)
            if not info:
                return {"error": "yt-dlp returned no metadata"}
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    return {"error": "Empty playlist"}
                info = entries[0]

            live_status = info.get("live_status") or "not_live"
            is_live = bool(info.get("is_live")) or live_status == "is_live"

            return {
                "title": info.get("title") or "Unknown",
                "channel": info.get("channel") or info.get("uploader") or "",
                "duration": float(info.get("duration") or 0.0),
                "is_live": is_live,
                "live_status": live_status,
                "was_live": bool(info.get("was_live")),
                "thumbnail": info.get("thumbnail") or "",
                "view_count": info.get("view_count"),
                "concurrent_view_count": info.get("concurrent_view_count"),
                "webpage_url": info.get("webpage_url") or clean,
            }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Live probe failed for {clean}: {e}")
        return {"error": str(e)}


# --------------------------------------------------------------------------- #
#  Record
# --------------------------------------------------------------------------- #
def _safe_name(text: str, fallback: str = "live") -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", (text or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:70] or fallback


def start_live_record(url: str, from_start: bool = False, title: Optional[str] = None) -> dict:
    """Starts a background yt-dlp recording. Returns {job_id, ...}."""
    clean = (url or "").strip()
    if not clean:
        raise ValueError("URL is required")

    job_id = uuid.uuid4().hex[:8]
    token = uuid.uuid4().hex[:6]
    safe_title = _safe_name(title or "Live Stream")
    out_name = f"{LIVE_PREFIX}{token}.mp4"
    out_path = UPLOADS_DIR / out_name

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    live_record_jobs[job_id] = {
        "job_id": job_id,
        "status": "starting",          # starting | recording | finishing | ready | stopped | failed
        "url": clean,
        "title": safe_title,
        "from_start": bool(from_start),
        "started_at": time.time(),
        "elapsed": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed": 0.0,
        "filename": out_name,
        "file_path": str(out_path),
        "video_url": None,
        "video_id": None,
        "error": None,
    }

    proc = _spawn_recorder(job_id, clean, out_path, from_start)
    _live_procs[job_id] = proc
    live_record_jobs[job_id]["status"] = "recording"
    return {"job_id": job_id, "status": "recording", "filename": out_name}


def _spawn_recorder(job_id: str, url: str, out_path: Path, from_start: bool) -> subprocess.Popen:
    """Launches yt-dlp as a child process, streaming progress on stdout."""
    # Reuse the repo's battle-tested yt-dlp base command (JS runtime for the
    # YouTube challenge solver, player_client fallbacks, --force-ipv4, cookies).
    # This is what makes live recording survive YouTube's bot checks.
    try:
        from backend.video_engine import get_yt_dlp_base_cmd
        cmd = list(get_yt_dlp_base_cmd(include_cookies=True))
    except Exception:  # noqa: BLE001
        cmd = [sys.executable, "-m", "yt_dlp"]
        cookies = get_effective_cookies_path()
        if cookies:
            cmd += ["--cookies", str(cookies)]

    cmd += [
        "--no-playlist",
        "--no-part",
        "--newline",
        "--progress",
        "--no-warnings",
        "--hls-use-mpegts",
        # Live streams: prefer a single muxed stream ("b") so the partial file is
        # already playable if the user stops early. Only fall back to separate
        # video+audio (which needs a final merge) when no muxed format exists.
        "-f", "b/bv*+ba",
        "--merge-output-format", "mp4",
        "--progress-template",
        "PROG|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.speed)s|%(progress.eta)s",
        "-o", str(out_path),
    ]

    if from_start:
        cmd.append("--live-from-start")

    # Prefer ffmpeg that the engine already resolved onto PATH.
    cmd.append(url)

    logger.info(f"[live {job_id}] recording: {' '.join(cmd[:-1])} <url>")

    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


async def pump_recorder_output(job_id: str):
    """Reads yt-dlp stdout and keeps the job dict updated. Runs as a task."""
    proc = _live_procs.get(job_id)
    job = live_record_jobs.get(job_id)
    if not proc or not job:
        return

    loop = asyncio.get_running_loop()

    def _read_line():
        try:
            return proc.stdout.readline() if proc.stdout else ""
        except Exception:
            return ""

    while True:
        line = await loop.run_in_executor(None, _read_line)
        if line == "":
            break
        line = line.strip()
        if not line:
            continue

        if line.startswith("PROG|"):
            parts = line.split("|")
            if len(parts) >= 5:
                try:
                    dl = int(float(parts[1])) if parts[1] not in ("NA", "None", "") else 0
                except Exception:
                    dl = 0
                try:
                    total = int(float(parts[2])) if parts[2] not in ("NA", "None", "") else 0
                except Exception:
                    total = 0
                try:
                    speed = float(parts[3]) if parts[3] not in ("NA", "None", "") else 0.0
                except Exception:
                    speed = 0.0
                job["downloaded_bytes"] = dl
                job["total_bytes"] = total
                job["speed"] = speed
        else:
            job["last_log"] = line[-300:]
            # Keep the most useful diagnostic line (yt-dlp prefixes real errors
            # with "ERROR:"), so the UI can show why a recording failed.
            upper = line.upper()
            if "ERROR" in upper or "SIGN IN" in upper or "BOT" in upper:
                job["error_log"] = line[-300:]

        job["elapsed"] = time.time() - job["started_at"]

        # Live HLS often does not report downloaded_bytes; fall back to the
        # size of the file growing on disk so the UI still shows progress.
        if not job["downloaded_bytes"]:
            try:
                p = Path(job["file_path"])
                if p.exists():
                    job["downloaded_bytes"] = p.stat().st_size
                else:
                    # yt-dlp downloads video/audio as separate part files and
                    # only merges into the final name at the end, so sum every
                    # temp file that shares this job's stem (.f137.mp4, .part…).
                    stem = p.stem
                    total_on_disk = 0
                    for f in p.parent.glob(stem + "*"):
                        try:
                            if f.is_file():
                                total_on_disk += f.stat().st_size
                        except Exception:
                            pass
                    if total_on_disk:
                        job["downloaded_bytes"] = total_on_disk
            except Exception:
                pass

    # Process finished (or was terminated)
    rc = proc.wait()
    job["elapsed"] = time.time() - job["started_at"]

    was_stopped = job.get("status") == "stopping" or job.get("stop_requested")

    # Give ffmpeg/yt-dlp a moment to flush the container to disk.
    await asyncio.sleep(0.5)

    size = out_path = Path(job["file_path"])
    size_ok = out_path.exists() and out_path.stat().st_size > 50 * 1024

    if size_ok:
        # Normalize the container so every downstream tool (ffprobe, browser
        # preview, Whisper) reads it cleanly. mpegts-in-mp4 sometimes needs it.
        await asyncio.to_thread(_finalize_recording, out_path)
        job["status"] = "ready"
        job["video_id"] = out_path.stem
        job["video_url"] = f"/api/video/{out_path.name}"
        job["size_bytes"] = out_path.stat().st_size
        job["downloaded_bytes"] = job["size_bytes"]
        logger.info(f"[live {job_id}] recording ready: {out_path.name} "
                    f"({job['size_bytes'] / 1048576:.1f} MB)")
    else:
        # No usable file. Distinguish a genuine failure (yt-dlp errored out,
        # e.g. YouTube bot-check / unavailable) from a normal early stop.
        diag = (job.get("error_log") or job.get("last_log") or "")
        looks_failed = rc not in (0, None) or "ERROR" in diag.upper() or "sign in" in diag.lower()
        if looks_failed:
            job["status"] = "failed"
            job["error"] = diag or f"yt-dlp exited with code {rc}"
        elif was_stopped:
            job["status"] = "stopped"
        else:
            job["status"] = "failed"
            job["error"] = diag or f"yt-dlp exited with code {rc}"
        logger.warning(f"[live {job_id}] no usable recording produced (rc={rc}, "
                       f"stopped={was_stopped}, failed={looks_failed})")

    _live_procs.pop(job_id, None)


def _finalize_recording(path: Path):
    """Remux to a clean, faststart mp4 without re-encoding (best effort)."""
    tmp = path.with_name(path.stem + "_fix.mp4")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
        if tmp.exists() and tmp.stat().st_size > 50 * 1024:
            shutil.move(str(tmp), str(path))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Live remux skipped for {path.name}: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def stop_live_record(job_id: str) -> dict:
    """Requests a graceful stop; yt-dlp finalizes the file on SIGTERM."""
    job = live_record_jobs.get(job_id)
    if not job:
        raise KeyError("job not found")

    proc = _live_procs.get(job_id)
    job["stop_requested"] = True
    job["status"] = "stopping"

    if proc and proc.poll() is None:
        try:
            if IS_WINDOWS:
                # yt-dlp spawns ffmpeg as a child; terminating only the parent
                # orphans ffmpeg. Kill the whole tree so the file gets finalized
                # and no encoder keeps writing in the background.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=20,
                )
            else:
                proc.send_signal(signal.SIGINT)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[live {job_id}] stop failed: {e}")
            try:
                proc.kill()
            except Exception:
                pass
    return {"job_id": job_id, "status": job["status"]}


def get_live_status(job_id: str) -> dict:
    job = live_record_jobs.get(job_id)
    if not job:
        raise KeyError("job not found")
    out = dict(job)
    if job["status"] == "recording":
        out["elapsed"] = time.time() - job["started_at"]
    return out


def stop_all_recordings():
    """Called on backend shutdown so no orphan yt-dlp keeps running."""
    for job_id in list(_live_procs.keys()):
        try:
            stop_live_record(job_id)
        except Exception:
            pass
