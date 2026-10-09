import { useCallback, useRef, useState } from 'react';

/**
 * FilmToolsPanel — "trailer + pecah part 60 detik + rekomendasi musik latar"
 * untuk alur film (sumber legal: Internet Archive / upload sendiri).
 *
 * Backend (sudah hidup):
 *   POST /api/film/segments        -> trailer + part berurutan hingga durasi tamat
 *   POST /api/film/bgm-recommend   -> rekomendasi musik legal (Archive.org)
 *   POST /api/film/bgm-download    -> unduh track (SSE) -> path + URL audio lokal
 *
 * Panel ini mengubah hasilnya menjadi klip (start/end) yang langsung bisa
 * dirender lewat pipeline /api/render-batch yang sudah ada, dan menyalurkan
 * track musik terpilih ke slot BGM studio.
 */

export interface FilmPart {
  index: number;
  label: string;
  start_time: number;
  end_time: number;
  duration: number;
  is_trailer: boolean;
}

export interface FilmSegmentsPlan {
  duration: number;
  part_seconds: number;
  part_count: number;
  total_parts_duration: number;
  trailer: FilmPart | null;
  parts: FilmPart[];
}

export interface BgmTrack {
  identifier: string;
  title: string;
  creator?: string;
  year?: string;
  duration?: string;
  audio_file?: string;
  download_url?: string;
  preview_url?: string;
  url?: string;
  mood?: string;
}

export interface NewFilmClip {
  title: string;
  start_time: number;
  end_time: number;
  caption?: string;
}

interface Props {
  videoUrl: string;
  duration: number;
  t: any;
  /** Film / source title used to auto-build "Title\nPart N" clip titles. */
  title?: string;
  /** LLM settings so the panel can request per-part caption recommendations. */
  aiProvider?: string;
  aiBaseUrl?: string;
  aiApiKey?: string;
  aiModel?: string;
  /** Output language name for captions ("auto" = keep the film's language). */
  language?: string;
  /** Hand generated parts/trailer to the studio as renderable clips. */
  onAddClips: (clips: NewFilmClip[]) => void;
  /** Route a downloaded track into the studio BGM slot. */
  onUseBgm: (filePath: string, fileName: string, audioUrl: string) => void;
  /** Name of the BGM currently loaded in the studio (for a small hint). */
  currentBgmName?: string;
}

const MOODS: string[] = [
  'epic', 'action', 'tense', 'sad', 'calm', 'happy', 'romantic', 'mysterious',
];

const fmtClock = (s: number): string => {
  if (!isFinite(s) || s < 0) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
};

/**
 * Build a two-line clip title: the film title on the first line and "Part N"
 * on the second, e.g. "Judul Film\nPart 1". Falls back to the part label when
 * no film title is available. The manual newline survives wrap_title_smart in
 * the renderer, so it renders as two lines on the video.
 */
const buildPartTitle = (filmTitle: string | undefined, label: string, index: number): string => {
  const partLine = (label || `Part ${index + 1}`).trim();
  const base = (filmTitle || '').trim();
  if (!base) return partLine;
  return `${base}\n${partLine}`;
};

export default function FilmToolsPanel({
  videoUrl,
  duration,
  t,
  title,
  aiProvider,
  aiBaseUrl,
  aiApiKey,
  aiModel,
  language,
  onAddClips,
  onUseBgm,
  currentBgmName,
}: Props) {
  const [partSeconds, setPartSeconds] = useState<number>(150);
  const [planning, setPlanning] = useState<boolean>(false);
  const [plan, setPlan] = useState<FilmSegmentsPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  // Per-part caption recommendations, keyed by part index.
  const [captions, setCaptions] = useState<Record<number, string>>({});
  const [captioning, setCaptioning] = useState<boolean>(false);
  const [captionError, setCaptionError] = useState<string | null>(null);

  const [mood, setMood] = useState<string>('epic');
  const [tracks, setTracks] = useState<BgmTrack[]>([]);
  const [recommending, setRecommending] = useState<boolean>(false);
  const [bgmError, setBgmError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [bgmHint, setBgmHint] = useState<string | null>(null);

  const sseAbortRef = useRef<AbortController | null>(null);

  const buildClips = useCallback((parts: FilmPart[], caps: Record<number, string>): NewFilmClip[] => (
    parts.map((p) => ({
      title: buildPartTitle(title, p.label, p.index),
      start_time: p.start_time,
      end_time: p.end_time,
      caption: caps[p.index] || undefined,
    }))
  ), [title]);

  const fetchCaptions = useCallback(async (parts: FilmPart[]): Promise<Record<number, string>> => {
    if (!parts.length) return {};
    setCaptioning(true);
    setCaptionError(null);
    try {
      const res = await fetch('/api/film/captions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: (title || '').trim() || 'Film',
          parts: parts.map((p) => ({ index: p.index, label: p.label })),
          language: language || 'auto',
          provider: aiProvider || null,
          base_url: aiBaseUrl || null,
          api_key: aiApiKey || null,
          model: aiModel || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t.captionFailed);
      }
      const data = await res.json();
      const map: Record<number, string> = {};
      (data?.captions || []).forEach((c: any) => {
        if (typeof c?.index === 'number' && c?.caption) map[c.index] = c.caption;
      });
      setCaptions(map);
      return map;
    } catch (e: any) {
      setCaptionError(e.message || t.captionFailed);
      return {};
    } finally {
      setCaptioning(false);
    }
  }, [title, language, aiProvider, aiBaseUrl, aiApiKey, aiModel, t.captionFailed]);

  const generatePlan = useCallback(async () => {
    if (!duration || duration <= 0) {
      setPlanError(t.planFailed);
      return;
    }
    setPlanning(true);
    setPlanError(null);
    try {
      const res = await fetch('/api/film/segments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          duration,
          part_seconds: partSeconds,
          trailer_seconds: partSeconds,
          include_trailer: false,
          video_url: videoUrl || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t.planFailed);
      }
      const data: FilmSegmentsPlan = await res.json();
      setPlan(data);
      // Auto-add every consecutive part straight away so the whole film can be
      // rendered as an ordered batch of clips without a click per part.
      if (data.parts?.length) {
        const caps = await fetchCaptions(data.parts);
        onAddClips(buildClips(data.parts, caps));
      }
    } catch (e: any) {
      setPlanError(e.message || t.planFailed);
    } finally {
      setPlanning(false);
    }
  }, [duration, partSeconds, videoUrl, onAddClips, t.planFailed, fetchCaptions, buildClips]);

  const addAllParts = useCallback(() => {
    if (!plan?.parts?.length) return;
    onAddClips(buildClips(plan.parts, captions));
  }, [plan, onAddClips, captions, buildClips]);

  const addPart = useCallback((p: FilmPart) => {
    onAddClips([{
      title: buildPartTitle(title, p.label, p.index),
      start_time: p.start_time,
      end_time: p.end_time,
      caption: captions[p.index] || undefined,
    }]);
  }, [onAddClips, title, captions]);

  const recommend = useCallback(async () => {
    setRecommending(true);
    setBgmError(null);
    setTracks([]);
    try {
      const res = await fetch('/api/film/bgm-recommend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mood, limit: 6 }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t.bgmFailed);
      }
      const data = await res.json();
      setTracks(Array.isArray(data.tracks) ? data.tracks : []);
    } catch (e: any) {
      setBgmError(e.message || t.bgmFailed);
    } finally {
      setRecommending(false);
    }
  }, [mood, t.bgmFailed]);

  const downloadTrack = useCallback(async (track: BgmTrack) => {
    const file = track.audio_file || '';
    if (!track.identifier || !file) return;
    sseAbortRef.current?.abort();
    const ctrl = new AbortController();
    sseAbortRef.current = ctrl;
    setDownloadingId(track.identifier);
    setBgmError(null);
    setBgmHint(null);
    try {
      const res = await fetch('/api/film/bgm-download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          identifier: track.identifier,
          file,
          title_hint: track.title || 'bgm',
        }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t.bgmFailed);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let done = false;
      while (!done) {
        const { done: streamDone, value } = await reader.read();
        if (streamDone) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() ?? '';
        for (const chunk of chunks) {
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            let evt: any;
            try { evt = JSON.parse(line.slice(6)); } catch { continue; }
            if (evt.error) throw new Error(evt.error);
            if (evt.done && evt.result) {
              const r = evt.result;
              onUseBgm(r.file_path, r.saved_name, r.audio_url);
              setBgmHint(t.bgmCurrent(r.saved_name));
              done = true;
              break;
            }
          }
          if (done) break;
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') setBgmError(e.message || t.bgmFailed);
    } finally {
      setDownloadingId(null);
      sseAbortRef.current = null;
    }
  }, [onUseBgm, t.bgmFailed, t.bgmCurrent]);

  const totalMinutes = plan ? `${Math.round(plan.duration / 60)}m` : '';

  return (
    <div className="film-tools-panel" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* ── Trailer + parts plan ── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '0.75rem' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', fontSize: '0.76rem', color: 'var(--text-muted)' }}>
          {t.partLengthLabel}
          <input
            type="number"
            min={5}
            max={3600}
            value={partSeconds}
            onChange={(e) => setPartSeconds(Math.max(5, Math.min(3600, Number(e.target.value) || 150)))}
            style={{
              width: '96px', padding: '0.45rem 0.6rem', borderRadius: '8px',
              border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.03)',
              color: 'var(--text-primary)', fontSize: '0.85rem',
            }}
          />
        </label>

        <button
          type="button"
          onClick={generatePlan}
          disabled={planning || !duration}
          style={{
            padding: '0.55rem 1.1rem', borderRadius: '9px',
            border: '1px solid rgba(168, 85, 247, 0.45)',
            background: 'linear-gradient(135deg, rgba(168, 85, 247, 0.28) 0%, rgba(124, 58, 237, 0.14) 100%)',
            color: '#fff', fontWeight: 700, fontSize: '0.82rem',
            cursor: planning || !duration ? 'not-allowed' : 'pointer',
            opacity: planning || !duration ? 0.55 : 1,
          }}
        >
          {planning ? t.generating : t.generateBtn}
        </button>
      </div>

      {planError && (
        <div style={{
          padding: '0.6rem 0.85rem', borderRadius: '9px',
          background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.35)',
          color: '#fca5a5', fontSize: '0.8rem',
        }}>
          {planError}
        </div>
      )}

      {captionError && (
        <div style={{
          padding: '0.6rem 0.85rem', borderRadius: '9px',
          background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.35)',
          color: '#fca5a5', fontSize: '0.8rem',
        }}>
          {captionError}
        </div>
      )}

      {plan && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.6rem' }}>
            <span className="group-badge" style={{ fontWeight: 600 }}>
              {t.planSummary(plan.part_count, totalMinutes)}
            </span>
            {plan.parts.length > 0 && (
              <button
                type="button"
                onClick={addAllParts}
                style={{
                  padding: '0.4rem 0.85rem', borderRadius: '8px',
                  border: '1px solid rgba(16, 185, 129, 0.45)',
                  background: 'rgba(16, 185, 129, 0.14)', color: '#6ee7b7',
                  fontWeight: 700, fontSize: '0.76rem', cursor: 'pointer',
                }}
              >
                ➕ {t.addAllParts}
              </button>
            )}
          </div>

          {plan.parts.length === 0 ? (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{t.partsEmpty}</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', maxHeight: '190px', overflowY: 'auto' }}>
              {plan.parts.map((p) => (
                <div
                  key={`${p.index}_${p.start_time}`}
                  style={{
                    display: 'flex', flexDirection: 'column', gap: '0.3rem',
                    padding: '0.4rem 0.6rem', borderRadius: '7px',
                    background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.6rem' }}>
                    <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      <strong style={{ color: 'var(--text-primary)' }}>{p.label}</strong>
                      {' · '}{fmtClock(p.start_time)}–{fmtClock(p.end_time)} ({Math.round(p.duration)}s)
                    </span>
                    <button
                      type="button"
                      onClick={() => addPart(p)}
                      style={{
                        flexShrink: 0, padding: '0.2rem 0.6rem', borderRadius: '6px',
                        border: '1px solid rgba(168, 85, 247, 0.4)',
                        background: 'rgba(168, 85, 247, 0.12)', color: 'var(--primary, #a855f7)',
                        fontWeight: 600, fontSize: '0.72rem', cursor: 'pointer',
                      }}
                    >
                      {t.addPart}
                    </button>
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {t.captionLabel}
                  </div>
                  <textarea
                    rows={2}
                    value={captions[p.index] ?? ''}
                    placeholder={captioning ? t.captionGenerating : t.captionPlaceholder}
                    onChange={(e) => setCaptions((prev) => ({ ...prev, [p.index]: e.target.value }))}
                    style={{
                      width: '100%', boxSizing: 'border-box', resize: 'vertical',
                      padding: '0.4rem 0.55rem', borderRadius: '7px',
                      border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.04)',
                      color: 'var(--text-primary)', fontSize: '0.76rem', fontFamily: 'inherit',
                    }}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── BGM recommendation ── */}
      <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '0.85rem', display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{t.bgmMoodLabel}</span>
          <select
            value={mood}
            onChange={(e) => setMood(e.target.value)}
            style={{
              padding: '0.42rem 0.6rem', borderRadius: '8px',
              border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(20,20,26,0.9)',
              color: 'var(--text-primary)', fontSize: '0.82rem',
            }}
          >
            {MOODS.map((m) => (
              <option key={m} value={m}>{(t.bgmMoods as Record<string, string>)[m] || m}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={recommend}
            disabled={recommending}
            style={{
              padding: '0.45rem 0.95rem', borderRadius: '9px',
              border: '1px solid rgba(56, 189, 248, 0.45)',
              background: 'linear-gradient(135deg, rgba(56, 189, 248, 0.25) 0%, rgba(14, 165, 233, 0.12) 100%)',
              color: '#fff', fontWeight: 700, fontSize: '0.8rem',
              cursor: recommending ? 'not-allowed' : 'pointer', opacity: recommending ? 0.55 : 1,
            }}
          >
            {recommending ? t.bgmRecommending : t.bgmRecommendBtn}
          </button>
        </div>

        {currentBgmName && !bgmHint && (
          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>{t.bgmCurrent(currentBgmName)}</div>
        )}
        {bgmHint && (
          <div style={{ fontSize: '0.76rem', color: '#6ee7b7' }}>🎧 {bgmHint}</div>
        )}
        {bgmError && (
          <div style={{
            padding: '0.55rem 0.8rem', borderRadius: '8px',
            background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.35)',
            color: '#fca5a5', fontSize: '0.78rem',
          }}>
            {bgmError}
          </div>
        )}

        {tracks.length === 0 && !recommending && !bgmError && (
          <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>{t.bgmNoTracks}</div>
        )}

        {tracks.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', maxHeight: '230px', overflowY: 'auto' }}>
            {tracks.map((tr) => {
              const isDl = downloadingId === tr.identifier;
              return (
                <div
                  key={tr.identifier}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.6rem',
                    padding: '0.45rem 0.65rem', borderRadius: '8px',
                    background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-primary)', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {tr.title || tr.identifier}
                    </div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                      {tr.creator ? `${tr.creator} · ` : ''}{tr.duration || ''}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => downloadTrack(tr)}
                    disabled={isDl}
                    style={{
                      flexShrink: 0, padding: '0.3rem 0.75rem', borderRadius: '7px',
                      border: '1px solid rgba(16, 185, 129, 0.45)',
                      background: 'rgba(16, 185, 129, 0.14)', color: '#6ee7b7',
                      fontWeight: 700, fontSize: '0.74rem',
                      cursor: isDl ? 'not-allowed' : 'pointer', opacity: isDl ? 0.6 : 1,
                    }}
                  >
                    {isDl ? t.bgmDownloading : t.bgmUseBtn}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
