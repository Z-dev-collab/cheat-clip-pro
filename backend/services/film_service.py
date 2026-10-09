"""
Archive.org film / video service for Cheat Clip Pro.

Legal, public source for the "auto-cari film + pilih part + preview" workflow:
the Internet Archive (archive.org), which hosts public-domain and
openly-licensed movies.

Everything here is plain HTTP — no browser, no captcha, no anti-bot bypass:
    * Advanced Search API  -> find items by title
    * Metadata API         -> list the real video files ("parts") of an item
    * Download endpoint    -> direct, Range-capable MP4/OGV URLs

Public functions used by the router:
    search_archive(query, limit)                 -> list[dict]
    get_item_parts(identifier)                   -> dict
    download_archive_file(identifier, file_name,
                          title_hint, progress)  -> Path (saved in UPLOADS_DIR)

The downloaded file lands in UPLOADS_DIR so the existing /api/analyze pipeline
(Whisper transcription + AI clip detection + preview) picks it up unchanged.
"""

from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, unquote

import httpx

from backend.config import UPLOADS_DIR, logger

ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA_URL = "https://archive.org/metadata/{identifier}"
ARCHIVE_DOWNLOAD_URL = "https://archive.org/download/{identifier}/{file_name}"
ARCHIVE_IMAGE_URL = "https://archive.org/services/img/{identifier}"
ARCHIVE_DETAILS_URL = "https://archive.org/details/{identifier}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CheatClipPro/1.0"
)

# Container formats we can actually hand to the clip pipeline (ffmpeg friendly).
VIDEO_EXTENSIONS = (
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov",
    ".ogv", ".mpg", ".mpeg", ".flv", ".ts", ".wmv",
)

# Formats that are derivatives/placeholders rather than the main feature.
_LOW_PRIORITY_FORMATS = ("512kb mpeg4", "h.264 ia", "ogg video", "flash video", "windows media")

_CHUNK = 1024 * 256


def _client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(timeout, connect=20.0, read=timeout),
        follow_redirects=True,
    )


def _is_video_file(name: str, fmt: str) -> bool:
    lower = (name or "").lower()
    if lower.endswith(VIDEO_EXTENSIONS):
        return True
    f = (fmt or "").lower()
    return any(token in f for token in ("mpeg4", "h.264", "matroska", "webm", "quicktime", "ogg video"))


def _human_size(size: object) -> str:
    try:
        n = float(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.2f} {unit}"
        n /= 1024
    return ""


def _human_length(length: object) -> str:
    try:
        secs = float(length)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return ""
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clean_text(value: object, limit: int = 400) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _download_url(identifier: str, file_name: str) -> str:
    safe_id = quote(identifier, safe="")
    safe_file = quote(file_name, safe="/")
    return ARCHIVE_DOWNLOAD_URL.format(identifier=safe_id, file_name=safe_file)


def _best_thumbnail(identifier: str) -> str:
    return ARCHIVE_IMAGE_URL.format(identifier=quote(identifier, safe=""))


# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

def search_archive(query: str, limit: int = 12) -> List[Dict[str, object]]:
    """Search archive.org movies by title and return lightweight result dicts."""
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(40, int(limit or 12)))
    search_q = f"title:({query}) AND mediatype:(movies)"

    params: List[tuple] = [
        ("q", search_q),
        ("rows", str(limit * 3)),
        ("page", "1"),
        ("output", "json"),
        ("sort[]", "downloads desc"),
    ]
    for field in ("identifier", "title", "year", "description", "downloads", "item_size", "mediatype"):
        params.append(("fl[]", field))

    try:
        with _client() as client:
            resp = client.get(ARCHIVE_SEARCH_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive search failed for %r: %s", query, exc)
        raise ValueError(f"Pencarian Archive.org gagal: {exc}") from exc

    docs = (payload.get("response") or {}).get("docs") or []

    results: List[Dict[str, object]] = []
    for doc in docs:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        results.append({
            "identifier": identifier,
            "title": _clean_text(doc.get("title"), 160) or identifier,
            "year": _clean_text(doc.get("year"), 12),
            "description": _clean_text(doc.get("description"), 280),
            "downloads": doc.get("downloads"),
            "item_size": _human_size(doc.get("item_size")),
            "poster": _best_thumbnail(identifier),
            "url": ARCHIVE_DETAILS_URL.format(identifier=identifier),
        })
        if len(results) >= limit:
            break

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Parts (video files inside one archive.org item)
# ─────────────────────────────────────────────────────────────────────────────

def get_item_parts(identifier: str) -> Dict[str, object]:
    """Return metadata + the list of streamable video files ('parts') for an item."""
    identifier = (identifier or "").strip()
    if not identifier:
        raise ValueError("Identifier Archive.org kosong.")

    try:
        with _client() as client:
            resp = client.get(ARCHIVE_METADATA_URL.format(identifier=quote(identifier, safe="")))
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive metadata failed for %r: %s", identifier, exc)
        raise ValueError(f"Gagal mengambil metadata Archive.org: {exc}") from exc

    if not payload or payload.get("is_dark"):
        raise ValueError("Item Archive.org tidak tersedia atau bersifat privat.")

    meta = payload.get("metadata") or {}
    files = payload.get("files") or []

    parts: List[Dict[str, object]] = []
    for f in files:
        name = f.get("name") or ""
        fmt = f.get("format") or ""
        if not _is_video_file(name, fmt):
            continue
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        try:
            length = float(f.get("length") or 0)
        except (TypeError, ValueError):
            length = 0.0
        parts.append({
            "file": name,
            "label": name.rsplit("/", 1)[-1],
            "format": fmt,
            "size_bytes": size,
            "size": _human_size(size),
            "length": length,
            "duration": _human_length(length),
            "download_url": _download_url(identifier, name),
            "preview_url": f"/api/film/preview/{quote(identifier, safe='')}/{quote(name, safe='/')}",
        })

    # Put the best (largest, non-derivative) files first.
    def _rank(p: Dict[str, object]) -> tuple:
        low = 1 if any(tok in str(p["format"]).lower() for tok in _LOW_PRIORITY_FORMATS) else 0
        return (low, -int(p["size_bytes"] or 0))

    parts.sort(key=_rank)

    return {
        "identifier": identifier,
        "title": _clean_text(meta.get("title"), 200) or identifier,
        "year": _clean_text(meta.get("year"), 12),
        "description": _clean_text(meta.get("description"), 800),
        "creator": _clean_text(meta.get("creator"), 120),
        "licenseurl": _clean_text(meta.get("licenseurl"), 200),
        "poster": _best_thumbnail(identifier),
        "url": ARCHIVE_DETAILS_URL.format(identifier=identifier),
        "parts": parts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Download (streams into UPLOADS_DIR so the existing /api/analyze pipeline runs)
# ─────────────────────────────────────────────────────────────────────────────

def _safe_local_name(identifier: str, file_name: str) -> str:
    base = file_name.rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(base)
    ext = ext or ".mp4"
    id_part = re.sub(r"[^A-Za-z0-9]+", "_", identifier)[:40].strip("_") or "item"
    stem_part = re.sub(r"[^A-Za-z0-9]+", "_", stem)[:60].strip("_") or "video"
    return f"archive_{id_part}_{stem_part}{ext.lower()}"


def download_archive_file(
    identifier: str,
    file_name: str,
    title_hint: str = "",
    progress: Optional[Callable[[str, str, int], None]] = None,
) -> Path:
    """Download one archive.org video file into UPLOADS_DIR with progress callbacks."""
    identifier = (identifier or "").strip()
    file_name = (file_name or "").strip()
    if not identifier or not file_name:
        raise ValueError("Identifier / nama file Archive.org tidak boleh kosong.")

    def report(stage: str, detail: str, pct: int = 10) -> None:
        if progress:
            try:
                progress(stage, detail, max(1, min(99, int(pct))))
            except Exception:  # noqa: BLE001
                pass

    url = _download_url(identifier, file_name)
    local_name = _safe_local_name(identifier, file_name)
    dest = UPLOADS_DIR / local_name
    tmp = dest.with_suffix(dest.suffix + ".part")

    report("Menghubungi Archive.org", f"Menyiapkan unduhan: {local_name}", 4)

    try:
        with _client(timeout=180.0) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ValueError(f"Archive.org menolak unduhan (HTTP {resp.status_code}).")
                total = int(resp.headers.get("content-length") or 0)
                done = 0
                last_pct = -1
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(_CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 96 / total)
                            if pct != last_pct:
                                last_pct = pct
                                report(
                                    "Mengunduh dari Archive.org",
                                    f"{_human_size(done)} / {_human_size(total)}",
                                    pct,
                                )
                        else:
                            mb = done / (1024 * 1024)
                            report("Mengunduh dari Archive.org", f"{mb:.1f} MB terunduh", 40)
    except Exception as exc:  # noqa: BLE001
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        logger.warning("archive download failed %s/%s: %s", identifier, file_name, exc)
        raise ValueError(f"Gagal mengunduh dari Archive.org: {exc}") from exc

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise ValueError("Unduhan Archive.org kosong — coba pilih part lain.")

    if dest.exists():
        try:
            dest.unlink()
        except OSError:
            pass
    tmp.replace(dest)

    report("Unduhan Selesai", f"File tersimpan: {dest.name}", 99)
    logger.info("archive download complete: %s (%s bytes)", dest.name, dest.stat().st_size)
    return dest


def cleanup_stale_film_downloads(max_age_hours: int = 72) -> int:
    """Remove cached Archive.org downloads older than max_age_hours. Returns count."""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for f in UPLOADS_DIR.glob("archive_*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    os.remove(f)
                    removed += 1
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("Archive download cleanup failed: %s", exc)
    return removed


# ─────────────────────────────────────────────────────────────────────────────
# Film segmentation: trailer + consecutive 60s parts until the film ends
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PART_SECONDS = 150.0
MIN_PART_SECONDS = 5.0
MAX_PART_SECONDS = 3600.0
DEFAULT_TRAILER_SECONDS = 60.0


def _best_trailer_window(
    duration: float,
    trailer_seconds: float,
    heatmap: Optional[List[Dict[str, float]]] = None,
) -> tuple:
    """Pick the most engaging contiguous window of `trailer_seconds` as the trailer.

    When a heatmap (list of {start_time, end_time, value}) is supplied the window
    with the highest average engagement wins; otherwise the trailer starts at 0.
    Returns (start_time, end_time).
    """
    duration = max(0.0, float(duration))
    window = min(max(1.0, float(trailer_seconds)), duration)
    if duration <= window:
        return (0.0, round(duration, 3))

    if not heatmap:
        return (0.0, round(window, 3))

    points = [
        (float(p.get("start_time", 0.0)), float(p.get("end_time", 0.0)), float(p.get("value", 0.0)))
        for p in heatmap
        if p.get("end_time") is not None
    ]
    if not points:
        return (0.0, round(window, 3))

    points.sort(key=lambda p: p[0])
    best_start = 0.0
    best_score = -1.0
    # Slide the window across the heatmap and score by average energy inside it.
    for p_start, _p_end, _v in points:
        if p_start + window > duration + 0.5:
            break
        inside = [v for (s, e, v) in points if e > p_start and s < p_start + window]
        if not inside:
            continue
        score = sum(inside) / len(inside)
        if score > best_score:
            best_score = score
            best_start = p_start
    return (round(best_start, 3), round(min(duration, best_start + window), 3))


def plan_film_segments(
    duration: float,
    part_seconds: float = DEFAULT_PART_SECONDS,
    trailer_seconds: float = DEFAULT_TRAILER_SECONDS,
    include_trailer: bool = True,
    heatmap: Optional[List[Dict[str, float]]] = None,
) -> Dict[str, object]:
    """Split a film into consecutive `part_seconds` clips until the duration ends.

    Returns a plan the frontend can hand straight to /api/render-batch:
        {
          duration, part_seconds, part_count, total_parts_duration,
          trailer: {index, label, start_time, end_time, is_trailer} | None,
          parts:   [{index, label, start_time, end_time}, ...]
        }
    """
    duration = max(0.0, float(duration or 0.0))
    try:
        part_seconds = float(part_seconds)
    except (TypeError, ValueError):
        part_seconds = DEFAULT_PART_SECONDS
    part_seconds = max(MIN_PART_SECONDS, min(MAX_PART_SECONDS, part_seconds))
    try:
        trailer_seconds = float(trailer_seconds)
    except (TypeError, ValueError):
        trailer_seconds = DEFAULT_TRAILER_SECONDS
    trailer_seconds = max(1.0, min(MAX_PART_SECONDS, trailer_seconds))

    parts: List[Dict[str, object]] = []
    if duration > 0:
        t = 0.0
        idx = 0
        while t < duration - 0.05:
            end = min(duration, t + part_seconds)
            # Fold a tiny tail (< 1s) into the previous part instead of emitting it.
            if end - t < 1.0 and parts:
                parts[-1]["end_time"] = round(end, 3)
                break
            parts.append({
                "index": idx,
                "label": f"Part {idx + 1}",
                "start_time": round(t, 3),
                "end_time": round(end, 3),
                "duration": round(end - t, 3),
                "is_trailer": False,
            })
            t = end
            idx += 1

    trailer = None
    if include_trailer and duration > 1.0:
        ts, te = _best_trailer_window(duration, trailer_seconds, heatmap)
        trailer = {
            "index": -1,
            "label": "Trailer",
            "start_time": ts,
            "end_time": te,
            "duration": round(te - ts, 3),
            "is_trailer": True,
        }

    total_parts_duration = round(sum(float(p["duration"]) for p in parts), 3)  # type: ignore[arg-type]
    return {
        "duration": round(duration, 3),
        "part_seconds": part_seconds,
        "part_count": len(parts),
        "total_parts_duration": total_parts_duration,
        "trailer": trailer,
        "parts": parts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Background-music recommendation (legal: Archive.org audio, public-domain / CC)
# ─────────────────────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS = (".mp3", ".ogg", ".m4a", ".flac", ".wav", ".opus", ".aac")

# Mood -> Archive.org audio search keywords. Used when the caller does not
# supply an explicit free-text query.
MOOD_KEYWORDS = {
    "epic": "epic orchestral cinematic trailer",
    "action": "action energetic drums battle",
    "tense": "tense suspense thriller dark",
    "sad": "sad emotional piano melancholy",
    "calm": "calm ambient relaxing peaceful",
    "happy": "happy upbeat uplifting joyful",
    "romantic": "romantic love theme strings",
    "mysterious": "mysterious dark ambient mystery",
}

# Preferred audio containers, best first.
_AUDIO_FORMAT_RANK = (".mp3", ".ogg", ".opus", ".m4a", ".aac", ".flac", ".wav")


def _is_audio_file(name: str, fmt: str) -> bool:
    lower = (name or "").lower()
    if lower.endswith(AUDIO_EXTENSIONS):
        return True
    f = (fmt or "").lower()
    return any(token in f for token in ("mp3", "ogg vorbis", "flac", "wave", "aac", "opus"))


def search_archive_audio(query: str, limit: int = 8) -> List[Dict[str, object]]:
    """Search archive.org for openly-licensed audio (music) matching `query`."""
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(30, int(limit or 8)))
    search_q = f"({query}) AND mediatype:(audio)"

    params: List[tuple] = [
        ("q", search_q),
        ("rows", str(limit * 3)),
        ("page", "1"),
        ("output", "json"),
        ("sort[]", "downloads desc"),
    ]
    for field in ("identifier", "title", "creator", "year", "licenseurl", "downloads", "item_size", "mediatype"):
        params.append(("fl[]", field))

    try:
        with _client() as client:
            resp = client.get(ARCHIVE_SEARCH_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive audio search failed for %r: %s", query, exc)
        raise ValueError(f"Pencarian musik Archive.org gagal: {exc}") from exc

    docs = (payload.get("response") or {}).get("docs") or []
    results: List[Dict[str, object]] = []
    for doc in docs:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        results.append({
            "identifier": identifier,
            "title": _clean_text(doc.get("title"), 160) or identifier,
            "creator": _clean_text(doc.get("creator"), 120),
            "year": _clean_text(doc.get("year"), 12),
            "licenseurl": _clean_text(doc.get("licenseurl"), 200),
            "downloads": doc.get("downloads"),
            "item_size": _human_size(doc.get("item_size")),
            "poster": _best_thumbnail(identifier),
            "url": ARCHIVE_DETAILS_URL.format(identifier=identifier),
        })
        if len(results) >= limit:
            break
    return results


def get_audio_track_file(identifier: str) -> Optional[Dict[str, object]]:
    """Return the best streamable audio file (name/size/length) for an item."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    try:
        with _client() as client:
            resp = client.get(ARCHIVE_METADATA_URL.format(identifier=quote(identifier, safe="")))
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive audio metadata failed for %r: %s", identifier, exc)
        return None

    if not payload or payload.get("is_dark"):
        return None

    candidates: List[Dict[str, object]] = []
    for f in payload.get("files") or []:
        name = f.get("name") or ""
        fmt = f.get("format") or ""
        if not _is_audio_file(name, fmt):
            continue
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        try:
            length = float(f.get("length") or 0)
        except (TypeError, ValueError):
            length = 0.0
        ext = os.path.splitext(name)[1].lower()
        rank = _AUDIO_FORMAT_RANK.index(ext) if ext in _AUDIO_FORMAT_RANK else len(_AUDIO_FORMAT_RANK)
        candidates.append({
            "file": name,
            "format": fmt,
            "size_bytes": size,
            "size": _human_size(size),
            "length": length,
            "duration": _human_length(length),
            "rank": rank,
        })

    if not candidates:
        return None
    # Prefer a compact, web-friendly format, then a reasonably sized file.
    candidates.sort(key=lambda c: (int(c["rank"]), int(c["size_bytes"]) or 10 ** 12))  # type: ignore[arg-type]
    best = candidates[0]
    best["download_url"] = _download_url(identifier, str(best["file"]))
    best["preview_url"] = f"/api/film/preview/{quote(identifier, safe='')}/{quote(str(best['file']), safe='/')}"
    return best


def recommend_bgm(
    mood: str = "epic",
    query: str = "",
    limit: int = 6,
    duration: float = 0.0,
) -> Dict[str, object]:
    """Recommend legal background music from Archive.org for a given mood.

    Resolves a concrete audio file for each track so the frontend can preview
    and download it with one click. Returns {mood, query, count, tracks}.
    """
    mood = (mood or "epic").strip().lower()
    explicit = (query or "").strip()
    keywords = explicit or MOOD_KEYWORDS.get(mood, MOOD_KEYWORDS["epic"])

    tracks = search_archive_audio(keywords, limit=max(1, min(12, int(limit or 6))))

    resolved: List[Dict[str, object]] = []
    for track in tracks:
        info = get_audio_track_file(str(track.get("identifier", "")))
        if not info:
            continue
        merged = dict(track)
        merged["audio_file"] = info.get("file")
        merged["audio_format"] = info.get("format")
        merged["size"] = info.get("size")
        merged["size_bytes"] = info.get("size_bytes")
        merged["length"] = info.get("length")
        merged["duration"] = info.get("duration")
        merged["download_url"] = info.get("download_url")
        merged["preview_url"] = info.get("preview_url")
        merged["mood"] = mood
        resolved.append(merged)
        if len(resolved) >= max(1, min(12, int(limit or 6))):
            break

    return {
        "mood": mood,
        "query": keywords,
        "count": len(resolved),
        "tracks": resolved,
    }


def download_archive_audio(
    identifier: str,
    file_name: str,
    title_hint: str = "",
    progress: Optional[Callable[[str, str, int], None]] = None,
) -> Path:
    """Download one archive.org audio file into UPLOADS_DIR as a bgm_* asset.

    The saved name starts with `bgm_` so the existing BGM pipeline
    (upload-bgm / render settings) accepts it unchanged.
    """
    identifier = (identifier or "").strip()
    file_name = (file_name or "").strip()
    if not identifier or not file_name:
        raise ValueError("Identifier / nama file audio Archive.org tidak boleh kosong.")

    def report(stage: str, detail: str, pct: int = 10) -> None:
        if progress:
            try:
                progress(stage, detail, max(1, min(99, int(pct))))
            except Exception:  # noqa: BLE001
                pass

    url = _download_url(identifier, file_name)
    base = file_name.rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(base)
    ext = ext or ".mp3"
    hint = re.sub(r"[^A-Za-z0-9]+", "_", (title_hint or stem))[:40].strip("_") or "track"
    local_name = f"bgm_{hint}{ext.lower()}"
    dest = UPLOADS_DIR / local_name
    tmp = dest.with_suffix(dest.suffix + ".part")

    report("Menghubungi Archive.org", f"Menyiapkan musik: {local_name}", 4)
    try:
        with _client(timeout=180.0) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ValueError(f"Archive.org menolak unduhan audio (HTTP {resp.status_code}).")
                total = int(resp.headers.get("content-length") or 0)
                done = 0
                last_pct = -1
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(_CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 96 / total)
                            if pct != last_pct:
                                last_pct = pct
                                report("Mengunduh musik", f"{_human_size(done)} / {_human_size(total)}", pct)
    except Exception as exc:  # noqa: BLE001
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        logger.warning("archive audio download failed %s/%s: %s", identifier, file_name, exc)
        raise ValueError(f"Gagal mengunduh musik dari Archive.org: {exc}") from exc

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise ValueError("Unduhan musik Archive.org kosong — coba pilih track lain.")

    if dest.exists():
        try:
            dest.unlink()
        except OSError:
            pass
    tmp.replace(dest)
    report("Unduhan Selesai", f"Musik tersimpan: {dest.name}", 99)
    logger.info("archive audio download complete: %s (%s bytes)", dest.name, dest.stat().st_size)
    return dest
