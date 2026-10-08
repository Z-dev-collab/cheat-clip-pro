import { useCallback, useEffect, useRef, useState } from 'react';
import { useLanguage } from '../locales';

/**
 * ArchiveFilmPanel — "cari film dari judul + pilih part + preview" untuk sumber
 * yang 100% legal: Internet Archive (archive.org), arsip film domain-publik.
 *
 * Alur:
 *   1. Cari judul  -> POST /api/film/search
 *   2. Pilih hasil -> GET  /api/film/parts/{identifier}  (daftar part/file video)
 *   3. Tinjau part -> <video> via /api/film/preview/... (proxy Range, tanpa unduh)
 *      + tandai segmen in/out opsional
 *   4. Unduh part  -> POST /api/film/download (SSE progress) lalu serahkan ke
 *      pipeline /api/analyze melalui onReady().
 */

export interface ArchivePickedVideo {
  videoId: string;
  filename: string;
  savedName: string;
  duration: number;
  videoUrl: string;
  filePath: string;
  width: number;
  height: number;
}

interface FilmResult {
  identifier: string;
  title: string;
  year?: string;
  description?: string;
  downloads?: number;
  item_size?: string;
  poster?: string;
  url?: string;
}

interface FilmPart {
  file: string;
  label: string;
  format: string;
  size?: string;
  size_bytes?: number;
  length?: number;
  duration?: string;
  download_url: string;
  preview_url: string;
}

interface FilmParts {
  identifier: string;
  title: string;
  year?: string;
  description?: string;
  creator?: string;
  licenseurl?: string;
  poster: string;
  url?: string;
  parts: FilmPart[];
}

interface Props {
  /** Called once a part is downloaded and ready to be analyzed. */
  onReady: (info: ArchivePickedVideo, title: string) => void;
  /** Optional: called with an in/out segment selection for the chosen part. */
  onSegment?: (startSecs: number | null, endSecs: number | null) => void;
}

const fmtTime = (s: number): string => {
  if (!isFinite(s) || s < 0) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
};

export function ArchiveFilmPanel({ onReady, onSegment }: Props) {
  const { t } = useLanguage();
  const a = t.archive;

  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<FilmResult[]>([]);
  const [searched, setSearched] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [partsData, setPartsData] = useState<FilmParts | null>(null);
  const [loadingParts, setLoadingParts] = useState(false);
  const [selectedPart, setSelectedPart] = useState<FilmPart | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [downloading, setDownloading] = useState(false);
  const [dlStage, setDlStage] = useState('');
  const [dlDetail, setDlDetail] = useState('');
  const [dlPct, setDlPct] = useState(0);
  const [ready, setReady] = useState(false);

  const [startSecs, setStartSecs] = useState<number | null>(null);
  const [endSecs, setEndSecs] = useState<number | null>(null);
  const [previewDur, setPreviewDur] = useState(0);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sseAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => { sseAbortRef.current?.abort(); }, []);

  const runSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    setSearched(true);
    setResults([]);
    setPartsData(null);
    setSelectedId(null);
    setSelectedPart(null);
    setReady(false);
    try {
      const res = await fetch('/api/film/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, limit: 12 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || a.searchFailed);
      setResults(data.results || []);
    } catch (e: any) {
      setError(e.message || a.searchFailed);
    } finally {
      setSearching(false);
    }
  }, [query, a.searchFailed]);

  const loadParts = useCallback(async (identifier: string) => {
    setSelectedId(identifier);
    setLoadingParts(true);
    setError(null);
    setPartsData(null);
    setSelectedPart(null);
    setReady(false);
    setStartSecs(null);
    setEndSecs(null);
    try {
      const res = await fetch(`/api/film/parts/${encodeURIComponent(identifier)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || a.partsFailed);
      setPartsData(data);
      if (data.parts?.length) setSelectedPart(data.parts[0]);
    } catch (e: any) {
      setError(e.message || a.partsFailed);
    } finally {
      setLoadingParts(false);
    }
  }, [a.partsFailed]);

  const startDownload = useCallback(async () => {
    if (!partsData || !selectedPart) return;
    setDownloading(true);
    setReady(false);
    setError(null);
    setDlStage(a.preparing);
    setDlDetail('');
    setDlPct(0);

    const controller = new AbortController();
    sseAbortRef.current = controller;

    try {
      const res = await fetch('/api/film/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          identifier: partsData.identifier,
          file: selectedPart.file,
          title_hint: partsData.title || selectedPart.label,
        }),
        signal: controller.signal,
      });
      if (!res.body) throw new Error('No response stream from server.');

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
            if (evt.stage) setDlStage(evt.stage);
            if (evt.detail) setDlDetail(evt.detail);
            if (typeof evt.overall_progress === 'number') setDlPct(evt.overall_progress);
            if (evt.done && evt.result) {
              const r = evt.result;
              const info: ArchivePickedVideo = {
                videoId: r.video_id,
                filename: r.title || selectedPart.label,
                savedName: r.saved_name,
                duration: r.duration || 0,
                videoUrl: r.video_url,
                filePath: '',
                width: r.width || 0,
                height: r.height || 0,
              };
              setReady(true);
              setDlPct(100);
              onReady(info, partsData.title || selectedPart.label);
              done = true;
              break;
            }
          }
          if (done) break;
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') setError(e.message || a.downloadFailed);
    } finally {
      setDownloading(false);
      sseAbortRef.current = null;
    }
  }, [partsData, selectedPart, onReady, a.preparing, a.downloadFailed]);

  const applySegment = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (onSegment) onSegment(startSecs, endSecs);
    if (startSecs != null && startSecs < v.duration) {
      v.currentTime = startSecs;
    }
  }, [startSecs, endSecs, onSegment]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {/* Search bar */}
      <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } }}
          placeholder={a.searchPlaceholder}
          style={{
            flex: '1 1 260px',
            padding: '0.7rem 0.9rem',
            borderRadius: '10px',
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.03)',
            color: 'var(--text-primary)',
            fontSize: '0.9rem',
          }}
        />
        <button
          type="button"
          onClick={runSearch}
          disabled={searching || !query.trim()}
          style={{
            padding: '0.7rem 1.4rem',
            borderRadius: '10px',
            border: '1px solid rgba(245, 158, 11, 0.45)',
            background: 'linear-gradient(135deg, rgba(245, 158, 11, 0.25) 0%, rgba(217, 119, 6, 0.12) 100%)',
            color: '#fff',
            fontWeight: 700,
            fontSize: '0.88rem',
            cursor: searching || !query.trim() ? 'not-allowed' : 'pointer',
            opacity: searching || !query.trim() ? 0.55 : 1,
          }}
        >
          {searching ? a.searching : a.searchBtn}
        </button>
      </div>

      <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
        {a.legalNotice}
      </div>

      {error && (
        <div style={{
          padding: '0.75rem 1rem',
          borderRadius: '10px',
          background: 'rgba(239, 68, 68, 0.12)',
          border: '1px solid rgba(239, 68, 68, 0.35)',
          color: '#fca5a5',
          fontSize: '0.85rem',
        }}>
          {error}
        </div>
      )}

      {/* Results */}
      {searched && !searching && results.length === 0 && !error && (
        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{a.noResults}</div>
      )}

      {results.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          gap: '0.75rem',
        }}>
          {results.map((r) => {
            const active = selectedId === r.identifier;
            return (
              <button
                key={r.identifier}
                type="button"
                onClick={() => loadParts(r.identifier)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  textAlign: 'left',
                  padding: '0.5rem',
                  borderRadius: '12px',
                  border: active ? '1px solid rgba(245, 158, 11, 0.65)' : '1px solid rgba(255,255,255,0.08)',
                  background: active ? 'rgba(245, 158, 11, 0.12)' : 'rgba(255,255,255,0.02)',
                  cursor: 'pointer',
                  gap: '0.4rem',
                  transition: 'all 0.18s ease',
                }}
              >
                <div style={{
                  width: '100%',
                  aspectRatio: '2 / 3',
                  borderRadius: '8px',
                  overflow: 'hidden',
                  background: 'rgba(255,255,255,0.04)',
                }}>
                  {r.poster && (
                    <img
                      src={r.poster}
                      alt={r.title}
                      loading="lazy"
                      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                    />
                  )}
                </div>
                <div style={{
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  lineHeight: 1.25,
                }}>
                  {r.title}
                </div>
                <div style={{ display: 'flex', gap: '0.4rem', fontSize: '0.68rem', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
                  {r.year && <span>{r.year}</span>}
                  {r.item_size && <span>💾 {r.item_size}</span>}
                  {typeof r.downloads === 'number' && <span>⬇ {r.downloads.toLocaleString()}</span>}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* Parts + preview */}
      {loadingParts && (
        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{a.loadingParts}</div>
      )}

      {partsData && !loadingParts && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
          padding: '1rem',
          borderRadius: '14px',
          border: '1px solid rgba(255,255,255,0.08)',
          background: 'rgba(255,255,255,0.02)',
        }}>
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            {partsData.poster && (
              <img
                src={partsData.poster}
                alt={partsData.title}
                style={{ width: '90px', borderRadius: '10px', objectFit: 'cover' }}
              />
            )}
            <div style={{ flex: '1 1 240px', minWidth: 0 }}>
              <div style={{ fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                {partsData.title}{partsData.year ? ` (${partsData.year})` : ''}
              </div>
              {partsData.creator && (
                <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                  {a.creator}: {partsData.creator}
                </div>
              )}
              {partsData.description && (
                <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: '0.4rem', lineHeight: 1.5 }}>
                  {partsData.description.slice(0, 260)}
                </div>
              )}
            </div>
          </div>

          {/* Part list */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-secondary)' }}>
              {a.partsLabel} ({partsData.parts.length})
            </div>
            {partsData.parts.map((p) => {
              const active = selectedPart?.file === p.file;
              return (
                <button
                  key={p.file}
                  type="button"
                  onClick={() => { setSelectedPart(p); setStartSecs(null); setEndSecs(null); setReady(false); }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '0.75rem',
                    padding: '0.6rem 0.85rem',
                    borderRadius: '10px',
                    border: active ? '1px solid rgba(59, 130, 246, 0.6)' : '1px solid rgba(255,255,255,0.07)',
                    background: active ? 'rgba(59, 130, 246, 0.12)' : 'rgba(255,255,255,0.02)',
                    cursor: 'pointer',
                    textAlign: 'left',
                    flexWrap: 'wrap',
                  }}
                >
                  <span style={{
                    fontSize: '0.82rem',
                    color: 'var(--text-primary)',
                    fontWeight: 600,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    maxWidth: '60%',
                  }}>
                    {p.label}
                  </span>
                  <span style={{ display: 'flex', gap: '0.6rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    {p.format && <span>{p.format}</span>}
                    {p.size && <span>💾 {p.size}</span>}
                    {p.duration && <span>⏱ {p.duration}</span>}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Preview */}
          {selectedPart && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              <div style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-secondary)' }}>
                {a.previewLabel}
              </div>
              <video
                ref={videoRef}
                key={selectedPart.preview_url}
                src={selectedPart.preview_url}
                controls
                preload="metadata"
                onLoadedMetadata={(e) => setPreviewDur((e.target as HTMLVideoElement).duration || 0)}
                style={{
                  width: '100%',
                  maxHeight: '340px',
                  borderRadius: '12px',
                  background: '#000',
                  border: '1px solid rgba(255,255,255,0.08)',
                }}
              />

              {/* Segment picker */}
              <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', alignItems: 'center' }}>
                <button
                  type="button"
                  onClick={() => {
                    const v = videoRef.current;
                    if (v) setStartSecs(Math.floor(v.currentTime));
                  }}
                  style={{
                    padding: '0.4rem 0.8rem', borderRadius: '8px',
                    border: '1px solid rgba(16, 185, 129, 0.4)', background: 'rgba(16, 185, 129, 0.12)',
                    color: '#6ee7b7', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer',
                  }}
                >
                  {a.markStart} {startSecs != null ? `(${fmtTime(startSecs)})` : ''}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const v = videoRef.current;
                    if (v) setEndSecs(Math.floor(v.currentTime));
                  }}
                  style={{
                    padding: '0.4rem 0.8rem', borderRadius: '8px',
                    border: '1px solid rgba(244, 63, 94, 0.4)', background: 'rgba(244, 63, 94, 0.12)',
                    color: '#fda4af', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer',
                  }}
                >
                  {a.markEnd} {endSecs != null ? `(${fmtTime(endSecs)})` : ''}
                </button>
                {(startSecs != null || endSecs != null) && (
                  <button
                    type="button"
                    onClick={() => { setStartSecs(null); setEndSecs(null); onSegment?.(null, null); }}
                    style={{
                      padding: '0.4rem 0.8rem', borderRadius: '8px',
                      border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.03)',
                      color: 'var(--text-muted)', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer',
                    }}
                  >
                    {a.clearSegment}
                  </button>
                )}
                {(startSecs != null || endSecs != null) && (
                  <button
                    type="button"
                    onClick={applySegment}
                    style={{
                      padding: '0.4rem 0.8rem', borderRadius: '8px',
                      border: '1px solid rgba(139, 92, 246, 0.4)', background: 'rgba(139, 92, 246, 0.14)',
                      color: '#c4b5fd', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer',
                    }}
                  >
                    {a.playSegment}
                  </button>
                )}
                <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                  {a.segmentHint} {previewDur > 0 ? `· ${a.duration}: ${fmtTime(previewDur)}` : ''}
                </span>
              </div>

              {/* Download */}
              <button
                type="button"
                onClick={startDownload}
                disabled={downloading}
                style={{
                  padding: '0.75rem 1.4rem',
                  borderRadius: '10px',
                  border: '1px solid rgba(59, 130, 246, 0.5)',
                  background: 'linear-gradient(135deg, rgba(59, 130, 246, 0.28) 0%, rgba(37, 99, 235, 0.14) 100%)',
                  color: '#fff',
                  fontWeight: 700,
                  fontSize: '0.9rem',
                  cursor: downloading ? 'not-allowed' : 'pointer',
                  opacity: downloading ? 0.6 : 1,
                  alignSelf: 'flex-start',
                }}
              >
                {downloading ? a.downloading : a.downloadBtn}
              </button>

              {downloading && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                  <div style={{ height: '8px', borderRadius: '999px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
                    <div style={{
                      width: `${Math.min(100, Math.max(0, dlPct))}%`,
                      height: '100%',
                      background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
                      transition: 'width 0.25s ease',
                    }} />
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {dlStage}{dlDetail ? ` — ${dlDetail}` : ''}
                  </div>
                </div>
              )}

              {ready && (
                <div style={{
                  padding: '0.7rem 1rem',
                  borderRadius: '10px',
                  background: 'rgba(16, 185, 129, 0.12)',
                  border: '1px solid rgba(16, 185, 129, 0.35)',
                  color: '#6ee7b7',
                  fontSize: '0.85rem',
                }}>
                  {a.readyNotice}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
