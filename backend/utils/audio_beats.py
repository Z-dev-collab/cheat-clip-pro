"""
Audio beat / rhythm analysis for the "jedag jedug" (beat-synced punch) auto-edit
and for the gameplay "epic moment" background-sound review.

Everything here is offline and dependency-light: FFmpeg decodes the audio track to
raw PCM and NumPy does the signal work. No API keys, no network.

Two public helpers:

* ``detect_beats``   -> estimate tempo (BPM), the phase-aligned beat grid, and a
                        confidence score for a clip so the renderer can punch the
                        zoom exactly on the beat.
* ``review_background_audio`` -> a human-readable "peninjauan suara latar" plus the
                        list of high-energy peaks (explosions, killstreaks, hype)
                        that mark the epic gameplay moments worth clipping.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # numpy is a hard requirement of the rest of the audio pipeline
    import numpy as np
except Exception:  # pragma: no cover - surfaced to the caller instead
    np = None  # type: ignore

from backend.config import logger


SR = 22050  # analysis sample rate


def _decode_mono_pcm(audio_path: str, sr: int = SR) -> Optional["np.ndarray"]:
    """Decode any media file's audio track to a mono float32 array via FFmpeg."""
    if np is None:
        return None
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", str(audio_path),
                "-vn", "-ac", "1", "-ar", str(sr),
                "-f", "s16le", "-",
            ],
            capture_output=True,
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Beat analysis could not decode audio: {exc}")
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    data = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    if data.size == 0:
        return None
    # Normalise to -1..1 so thresholds are amplitude-independent.
    peak = float(np.max(np.abs(data)))
    if peak > 1.0:
        data = data / peak
    return data


def _onset_envelope(signal: "np.ndarray", sr: int = SR) -> Tuple["np.ndarray", float]:
    """Half-wave rectified energy-flux onset envelope (one value per frame)."""
    hop = 512
    win = 1024
    if signal.size < win:
        return np.zeros(0, dtype=np.float32), float(hop) / sr
    n_frames = (signal.size - win) // hop
    if n_frames <= 1:
        return np.zeros(0, dtype=np.float32), float(hop) / sr
    # Windowed RMS energy per frame (vectorised).
    idx = np.arange(win)[None, :] + (np.arange(n_frames)[:, None] * hop)
    frames = signal[idx]
    energy = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    flux = np.diff(energy)
    flux = np.clip(flux, 0.0, None)  # half-wave rectify
    frame_dt = float(hop) / sr
    return flux.astype(np.float32), frame_dt


def _estimate_tempo(onset: "np.ndarray", frame_dt: float) -> Tuple[float, float]:
    """Return (bpm, confidence) from the onset envelope via autocorrelation."""
    if onset.size < 8 or float(np.max(onset)) <= 0.0:
        return 0.0, 0.0
    x = onset - float(np.mean(onset))
    ac = np.correlate(x, x, mode="full")[onset.size - 1:]
    if ac[0] <= 1e-9:
        return 0.0, 0.0
    ac = ac / ac[0]

    min_bpm, max_bpm = 60.0, 200.0
    min_lag = max(1, int(round((60.0 / max_bpm) / frame_dt)))
    max_lag = min(ac.size - 1, int(round((60.0 / min_bpm) / frame_dt)))
    if max_lag <= min_lag:
        return 0.0, 0.0

    lags = np.arange(min_lag, max_lag + 1)
    ac_vals = ac[min_lag:max_lag + 1]

    # Log-normal tempo prior centred on ~120 BPM (Klapuri-style weighting):
    # this resolves the classic half/double-time ambiguity (174 vs 87 BPM).
    bpm_cand = 60.0 / (lags * frame_dt)
    prior = np.exp(-0.5 * ((np.log2(bpm_cand / 120.0)) / 0.9) ** 2)
    weighted = ac_vals * prior
    best_i = int(np.argmax(weighted))
    best_lag = int(lags[best_i])
    best_val = float(ac_vals[best_i])

    # If a plain octave (half/double) has a clearly stronger raw correlation and
    # the prior is not strongly opposed, prefer it for a musical grid.
    for factor in (2, 3):
        for cand in (best_lag * factor, best_lag // factor if best_lag % factor == 0 else 0):
            if cand and min_lag <= cand <= max_lag and ac[cand] > best_val * 1.15:
                best_lag, best_val = cand, float(ac[cand])

    # Prefer the true (faster) beat rate: a perfectly periodic track correlates
    # almost as strongly at half the lag, which would otherwise fold 174 BPM to
    # 87 BPM and halve the punch rate. Only switch when the halved lag is nearly
    # as strong, so genuinely slow tracks (90 BPM) keep their real tempo.
    half_lag = int(round(best_lag / 2.0))
    if half_lag >= min_lag and half_lag < best_lag and ac[half_lag] > best_val * 0.90:
        best_lag, best_val = half_lag, float(ac[half_lag])

    bpm = 60.0 / (best_lag * frame_dt)

    # Parabolic interpolation around the autocorrelation peak for sub-frame lag
    # precision — without this the grid drifts by ~1-2 beats across a 60s clip.
    if 0 < best_lag < ac.size - 1:
        y0, y1, y2 = float(ac[best_lag - 1]), float(ac[best_lag]), float(ac[best_lag + 1])
        denom = (y0 - 2.0 * y1 + y2)
        if abs(denom) > 1e-9:
            delta = 0.5 * (y0 - y2) / denom
            if -1.0 < delta < 1.0:
                refined_lag = best_lag + delta
                if refined_lag > 0:
                    bpm = 60.0 / (refined_lag * frame_dt)

    confidence = max(0.0, min(1.0, best_val))
    return round(bpm, 2), round(confidence, 3)


def _beat_phase(onset: "np.ndarray", frame_dt: float, bpm: float) -> float:
    """Find the beat-grid phase (seconds) that best aligns to onset energy."""
    if bpm <= 0 or onset.size == 0:
        return 0.0
    period = 60.0 / bpm
    if period <= frame_dt:
        return 0.0
    n_steps = max(2, int(round(period / frame_dt)))
    best_off, best_score = 0, -1.0
    for off in range(n_steps):
        idx = np.arange(off, onset.size, n_steps)
        score = float(np.sum(onset[idx])) if idx.size else 0.0
        if score > best_score:
            best_score, best_off = score, off
    return best_off * frame_dt


def detect_beats(audio_path: str) -> Dict[str, Any]:
    """
    Estimate the tempo and beat grid of an audio/video file.

    Returns a dict:
        { "ok": bool, "bpm": float, "confidence": float,
          "beat_period": float, "first_beat": float, "beat_count": int,
          "beat_times": [..first 64 beat times..] }
    On any failure it returns ``ok=False`` with a safe 120 BPM fallback so the
    renderer still produces a musical punch.
    """
    fallback = {
        "ok": False, "bpm": 120.0, "confidence": 0.0,
        "beat_period": 0.5, "first_beat": 0.0, "beat_count": 0, "beat_times": [],
    }
    if np is None:
        return fallback
    signal = _decode_mono_pcm(audio_path)
    if signal is None or signal.size < SR // 2:
        return fallback

    onset, frame_dt = _onset_envelope(signal)
    bpm, conf = _estimate_tempo(onset, frame_dt)
    if bpm <= 0:
        return fallback

    period = 60.0 / bpm
    first = _beat_phase(onset, frame_dt, bpm)
    duration = signal.size / SR
    beat_times: List[float] = []
    t = first
    while t <= duration + 1e-6 and len(beat_times) < 100000:
        if t >= 0:
            beat_times.append(round(t, 3))
        t += period

    return {
        "ok": True,
        "bpm": bpm,
        "confidence": conf,
        "beat_period": round(period, 4),
        "first_beat": round(first, 3),
        "beat_count": len(beat_times),
        "beat_times": beat_times[:64],
    }


def review_background_audio(audio_path: str, duration: float = 0.0) -> Dict[str, Any]:
    """
    "Peninjauan suara latar": inspect the background audio of a clip to locate
    the epic / high-energy gameplay moments (explosions, killstreaks, hype peaks)
    and describe the overall loudness dynamics.

    Returns:
        { "ok": bool, "duration": float, "peak_count": int,
          "loudness": float (0..1), "dynamics": float (0..1),
          "epic_peaks": [ {time, score}, ... ],   # sorted desc, seconds
          "summary": "..." }
    """
    empty = {
        "ok": False, "duration": round(duration, 2), "peak_count": 0,
        "loudness": 0.0, "dynamics": 0.0, "epic_peaks": [], "summary": "",
    }
    if np is None:
        return empty
    signal = _decode_mono_pcm(audio_path)
    if signal is None or signal.size == 0:
        return empty

    sr = SR
    total = signal.size / sr
    if duration <= 0:
        duration = total

    # Energy envelope on ~0.25s windows.
    hop = int(sr * 0.25)
    win = hop
    n = max(1, signal.size // hop)
    energy = np.empty(n, dtype=np.float32)
    for i in range(n):
        chunk = signal[i * hop:(i + 1) * hop]
        energy[i] = float(np.sqrt(np.mean(chunk * chunk) + 1e-12)) if chunk.size else 0.0

    mean_e = float(np.mean(energy))
    std_e = float(np.std(energy))
    max_e = float(np.max(energy)) or 1e-6
    loudness = float(np.clip(mean_e / max_e, 0.0, 1.0))
    dynamics = float(np.clip((std_e / (mean_e + 1e-6)) / 1.5, 0.0, 1.0))

    # Peaks: local maxima above mean + 0.8*std, merged within ~1s.
    thr = mean_e + 0.8 * std_e
    peaks: List[Dict[str, float]] = []
    min_gap_frames = 4  # 4 * 0.25s = 1s
    last_idx = -min_gap_frames - 1
    for i in range(1, n - 1):
        if energy[i] >= energy[i - 1] and energy[i] > energy[i + 1] and energy[i] >= thr:
            if i - last_idx < min_gap_frames:
                if peaks and energy[i] > peaks[-1]["score"]:
                    peaks[-1] = {"time": round(i * 0.25, 2),
                                 "score": round(float(energy[i] / max_e), 3)}
                    last_idx = i
                continue
            peaks.append({"time": round(i * 0.25, 2), "score": round(float(energy[i] / max_e), 3)})
            last_idx = i

    peaks.sort(key=lambda p: p["score"], reverse=True)
    peaks = peaks[:24]

    if not peaks:
        summary = "Suara latar relatif datar — tidak ada lonjakan energi yang menonjol."
    else:
        summary = (
            f"Terdeteksi {len(peaks)} lonjakan energi (momen epik) pada suara latar; "
            f"dinamika {int(dynamics * 100)}%, kenyaringan rata-rata {int(loudness * 100)}%."
        )

    return {
        "ok": True,
        "duration": round(duration, 2),
        "peak_count": len(peaks),
        "loudness": round(loudness, 3),
        "dynamics": round(dynamics, 3),
        "epic_peaks": peaks,
        "summary": summary,
    }
