"""
Generic video source support for Cheat Clip Pro.

Lets the app clip videos from ANY site yt-dlp understands — not just YouTube.
This is what powers the "film per part" workflow: paste a link from a streaming
site (lk21, idlix, rebahin, a direct .mp4/.m3u8, an embed player, ...) and the
backend downloads that part locally, transcribes it offline with Whisper, runs
the normal AI analysis and cuts the clips.

The download is cached on disk by a slug of the URL, so re-analyzing the same
link (e.g. after tweaking settings) is instant.

It deliberately shells out to the same `yt-dlp` base command the rest of the app
uses (JS runtime + player-client fallbacks + --force-ipv4 + cookies) so behaviour
matches the proven live-recording path.

Public surface:
    is_generic_video_url(url)                 -> bool
    fetch_generic_metadata(url)               -> dict
    download_generic_video(url, on_progress)  -> Path
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

from backend.config import UPLOADS_DIR, logger
from backend.video_engine import get_yt_dlp_base_cmd

# Progress callback: (stage: str, detail: str, step_pct: int) -> None
ProgressCb = Optional[Callable[[str, str, int], None]]

_IS_WINDOWS = sys.platform.startswith("win")
_CREATE_NO_WINDOW = 0x08000000 if _IS_WINDOWS else 0


def _ffprobe_bin() -> str:
    """Locate ffprobe: PATH first, then beside ffmpeg, then the bare name."""
    import shutil
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cand = Path(ffmpeg).with_name("ffprobe" + (".exe" if _IS_WINDOWS else ""))
        if cand.exists():
            return str(cand)
    return "ffprobe"


def _has_video_stream(path: Path) -> bool:
    """True if ffprobe sees a real, playable video stream in the file.

    Replaces a naive size threshold: a short Telegram clip can be a few hundred
    KB yet perfectly valid, while a failed download can leave a >512 KB HTML
    error page that is not a video at all.
    """
    try:
        if not path.exists() or path.stat().st_size < 2048:
            return False
    except Exception:
        return False
    try:
        proc = _run(
            [
                _ffprobe_bin(), "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout=20,
        )
        return proc.returncode == 0 and "video" in (proc.stdout or "").lower()
    except Exception:
        # If ffprobe is unavailable, fall back to a conservative size check.
        try:
            return path.stat().st_size > 64 * 1024
        except Exception:
            return False


def _slug_for_url(url: str) -> str:
    """Stable, filesystem-safe cache key derived from a URL."""
    host = (urlparse(url).netloc or "site").replace("www.", "")
    host = re.sub(r"[^a-zA-Z0-9]+", "", host)[:24] or "site"
    digest = hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:12]
    return f"{host}_{digest}"


def is_generic_video_url(url: str) -> bool:
    """True for any absolute http(s) URL that is not YouTube or Google Drive.

    Those two have dedicated, richer paths upstream, so this is really the
    "everything else" detector: streaming/film sites, direct media links, embeds.
    """
    if not url:
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    if "drive.google.com" in host or "docs.google.com" in host:
        return False
    if "youtube.com" in host or "youtu.be" in host:
        return False
    return True


def _base_cmd() -> list:
    """The shared yt-dlp base command (JS runtime, player_client, ipv4, cookies)."""
    try:
        return list(get_yt_dlp_base_cmd(include_cookies=True))
    except Exception:  # noqa: BLE001
        return [sys.executable, "-m", "yt_dlp"]


def _run(cmd: list, timeout: int = 120) -> subprocess.CompletedProcess:
    kwargs = {}
    if _IS_WINDOWS:
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **kwargs,
    )


def fetch_generic_metadata(url: str) -> Dict[str, object]:
    """Best-effort metadata probe (title, duration, thumbnail) via yt-dlp -J."""
    cmd = _base_cmd() + ["--no-playlist", "--no-warnings", "-J", url]
    try:
        proc = _run(cmd, timeout=90)
        raw = (proc.stdout or "").strip()
        if not raw:
            return {}
        # yt-dlp -J prints the JSON object; take the last non-empty line to be safe.
        line = raw.splitlines()[-1]
        info = json.loads(line)
        if not info:
            return {}
        # A page may resolve to a playlist of episodes/parts; take the first entry.
        if info.get("_type") == "playlist" and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if entries:
                info = entries[0]
        return {
            "title": info.get("title") or "",
            "channel": info.get("uploader") or info.get("channel") or urlparse(url).netloc,
            "duration": float(info.get("duration") or 0.0),
            "thumbnail": info.get("thumbnail") or "",
            "is_live": bool(info.get("is_live") or False),
            "live_status": info.get("live_status") or "not_live",
        }
    except Exception as exc:  # noqa: BLE001 - metadata is optional, never fatal
        logger.warning(f"Generic metadata probe failed for {url}: {exc}")
        return {}


def download_generic_video(url: str, on_progress: ProgressCb = None) -> Path:
    """Downloads a video from any yt-dlp supported site into UPLOADS_DIR.

    Returns the local file Path. Raises ValueError with a friendly message when
    the site is unsupported, login-walled, or the download fails.
    """
    slug = _slug_for_url(url)

    # 1. Cache check — reuse a previous download of the same link.
    cached = [
        f for f in UPLOADS_DIR.glob(f"site_{slug}*.*")
        if f.is_file() and _has_video_stream(f)
    ]
    if cached:
        cached.sort(key=lambda p: p.stat().st_size, reverse=True)
        best = cached[0]
        logger.info(f"Using cached site video: {best.name} ({best.stat().st_size} bytes)")
        if on_progress:
            on_progress("Video Found in Cache", f"Using cached download: {best.name}", 100)
        return best

    host = urlparse(url).netloc
    if on_progress:
        on_progress("Connecting to Site", f"Resolving media stream from {host}...", 5)

    out_template = str(UPLOADS_DIR / f"site_{slug}.%(ext)s")
    cmd = _base_cmd() + [
        "--no-playlist",
        "--no-part",
        "--newline",
        "--progress",
        "--no-warnings",
        "--hls-use-mpegts",
        # Prefer a muxed single stream so the file stays usable even if the
        # download is interrupted; fall back to best video+audio with a merge.
        "-f", "b/bv*+ba",
        "--merge-output-format", "mp4",
        "--retries", "3",
        "--fragment-retries", "5",
        "--concurrent-fragments", "4",
        "--progress-template",
        "PROG|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s",
        "-o", out_template,
        url,
    ]

    logger.info(f"[site] downloading: {' '.join(cmd[:-1])} <url>")

    kwargs = {}
    if _IS_WINDOWS:
        kwargs["creationflags"] = _CREATE_NO_WINDOW

    last_tail = ""
    try:
        proc = subprocess.Popen(
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
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            last_tail = line[-400:]
            if line.startswith("PROG|") and on_progress:
                parts = line.split("|")
                if len(parts) >= 5:
                    def _num(v):
                        try:
                            return float(v)
                        except Exception:
                            return 0.0
                    downloaded = _num(parts[1])
                    total = _num(parts[2]) or _num(parts[3])
                    speed = _num(parts[4]) if len(parts) > 4 else 0.0
                    if total > 0:
                        pct = int(min(88, 8 + (downloaded / total) * 80))
                        spd = f" @ {speed / (1024 * 1024):.1f} MB/s" if speed else ""
                        on_progress(
                            "Downloading Video",
                            f"Downloading from {host}: {downloaded / (1024 * 1024):.1f} MB / "
                            f"{total / (1024 * 1024):.1f} MB ({pct}%){spd}",
                            pct,
                        )
                    else:
                        on_progress(
                            "Downloading Video",
                            f"Downloading from {host}: {downloaded / (1024 * 1024):.1f} MB...",
                            40,
                        )
        proc.wait(timeout=120)
        returncode = proc.returncode
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to run the downloader: {exc}")

    candidates = [
        f for f in UPLOADS_DIR.glob(f"site_{slug}*.*")
        if f.is_file() and _has_video_stream(f)
    ]
    if returncode != 0 and not candidates:
        raise ValueError(
            "Could not download a video from this link. "
            f"Downloader said: {last_tail or 'unknown error'}. "
            "If this is a film streaming site, try the direct video/embed link "
            "(often ending in .mp4 or .m3u8) instead of the landing page."
        )
    if not candidates:
        # Telegram gets a targeted hint: most failures are a channel/group root
        # URL (no single post) or a text-only post, not a broken downloader.
        if (urlparse(url).netloc or "").lower().endswith("t.me"):
            raise ValueError(
                "No video found at this Telegram link. Make sure you copied the link "
                "to a specific post that contains a video (it looks like "
                "https://t.me/channel/123), not the channel root "
                "(https://t.me/channel) or a text-only message."
            )
        raise ValueError(
            "The download produced no usable video file. The site may require a "
            "login, may serve the film through a JavaScript-only player, or may "
            "block automated downloads."
        )

    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    result = candidates[0]
    # Normalise a possible double extension (.mp4.mp4).
    if result.name.endswith(".mp4.mp4"):
        fixed = result.with_name(result.name[:-4])
        try:
            result.rename(fixed)
            result = fixed
        except Exception:
            pass
    logger.info(f"Generic site video downloaded: {result.name} ({result.stat().st_size} bytes)")
    if on_progress:
        on_progress("Download Complete", f"Saved {result.name}", 100)
    return result


def cleanup_stale_generic_downloads(max_age_hours: int = 48) -> int:
    """Removes cached generic downloads older than max_age_hours. Returns count."""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for f in UPLOADS_DIR.glob("site_*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    os.remove(f)
                    removed += 1
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Stale generic download cleanup failed: {exc}")
    return removed
