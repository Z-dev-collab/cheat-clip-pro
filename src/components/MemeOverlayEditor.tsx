import React, { useRef, useState } from 'react';
import type { MemeOverlay } from '../types';

interface MemeOverlayEditorProps {
  overlays: MemeOverlay[];
  onChange: (overlays: MemeOverlay[]) => void;
  /** Studio translations (`t.studio`). */
  t: any;
  /** Duration of the clip in seconds, used to bound the timing inputs. */
  clipDuration?: number;
}

let memeIdCounter = 0;
const nextMemeId = () => `meme_${Date.now()}_${memeIdCounter++}`;

const DEFAULT_TEXT_OVERLAY = (): MemeOverlay => ({
  id: nextMemeId(),
  type: 'text',
  text: '',
  x: 50,
  y: 25,
  size: 8,
  startTime: 0,
  endTime: null,
  opacity: 1,
  fontColor: '#ffffff',
  outlineColor: '#000000',
  rotation: 0,
});

const DEFAULT_IMAGE_OVERLAY = (): MemeOverlay => ({
  id: nextMemeId(),
  type: 'image',
  imagePath: '',
  imageUrl: '',
  x: 50,
  y: 50,
  size: 25,
  startTime: 0,
  endTime: null,
  opacity: 1,
  rotation: 0,
});

const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

const MemeOverlayEditor: React.FC<MemeOverlayEditorProps> = ({ overlays, onChange, t, clipDuration }) => {
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const update = (id: string, patch: Partial<MemeOverlay>) => {
    onChange(overlays.map((o) => (o.id === id ? { ...o, ...patch } : o)));
  };

  const remove = (id: string) => {
    onChange(overlays.filter((o) => o.id !== id));
  };

  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= overlays.length) return;
    const copy = [...overlays];
    [copy[index], copy[target]] = [copy[target], copy[index]];
    onChange(copy);
  };

  const addText = () => {
    const o = DEFAULT_TEXT_OVERLAY();
    onChange([...overlays, o]);
    setExpandedId(o.id);
  };

  const addImage = () => {
    const o = DEFAULT_IMAGE_OVERLAY();
    onChange([...overlays, o]);
    setExpandedId(o.id);
  };

  const uploadImage = async (id: string, file: File) => {
    if (!file) return;
    setUploadingId(id);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/upload-meme', { method: 'POST', body: form });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      update(id, { imagePath: data.file_path, imageUrl: data.url });
    } catch (err: any) {
      console.error('Meme upload failed:', err);
      alert(`Upload meme gagal: ${err?.message || err}`);
    } finally {
      setUploadingId(null);
    }
  };

  const preset = (id: string, x: number, y: number) => update(id, { x, y });

  return (
    <div className="meme-overlay-editor">
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        <button type="button" className="pill-btn" onClick={addText} style={{ flex: '1 1 auto' }}>
          {t.memeAddText}
        </button>
        <button type="button" className="pill-btn" onClick={addImage} style={{ flex: '1 1 auto' }}>
          {t.memeAddImage}
        </button>
      </div>

      {overlays.length === 0 && (
        <p className="bgm-hint-text" style={{ marginTop: 0 }}>{t.memeEmpty}</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
        {overlays.map((o, index) => {
          const isExpanded = expandedId === o.id;
          return (
            <div
              key={o.id}
              className="meme-overlay-item"
              style={{
                border: '1px solid var(--border-color)',
                borderRadius: '10px',
                padding: '0.6rem 0.7rem',
                background: 'var(--bg-surface-hover)',
              }}
            >
              {/* Item header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--text-secondary)', minWidth: '3.2rem' }}>
                  {o.type === 'text' ? t.memeTypeText : t.memeTypeImage}
                </span>
                <span
                  style={{
                    flex: 1,
                    fontSize: '0.8rem',
                    color: '#fff',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {o.type === 'text'
                    ? (o.text?.trim() || t.memeTextPlaceholder)
                    : (o.imageUrl ? '🖼️' : t.memeUploadMain)}
                </span>
                <button
                  type="button"
                  className="pill-btn"
                  style={{ padding: '0.15rem 0.5rem', fontSize: '0.7rem' }}
                  onClick={() => setExpandedId(isExpanded ? null : o.id)}
                >
                  {isExpanded ? '▲' : '▼'}
                </button>
                <button
                  type="button"
                  className="pill-btn"
                  title={t.memeMoveUp}
                  style={{ padding: '0.15rem 0.4rem', fontSize: '0.7rem' }}
                  onClick={() => move(index, -1)}
                  disabled={index === 0}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="pill-btn"
                  title={t.memeMoveDown}
                  style={{ padding: '0.15rem 0.4rem', fontSize: '0.7rem' }}
                  onClick={() => move(index, 1)}
                  disabled={index === overlays.length - 1}
                >
                  ↓
                </button>
                <button
                  type="button"
                  style={{
                    background: 'rgba(239, 68, 68, 0.15)',
                    color: '#f87171',
                    border: '1px solid rgba(239, 68, 68, 0.3)',
                    fontSize: '0.7rem',
                    padding: '0.15rem 0.5rem',
                    borderRadius: '5px',
                    cursor: 'pointer',
                    fontWeight: 600,
                  }}
                  onClick={() => remove(o.id)}
                >
                  {t.memeRemoveBtn}
                </button>
              </div>

              {isExpanded && (
                <div style={{ marginTop: '0.7rem', display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
                  {/* Content: text or image */}
                  {o.type === 'text' ? (
                    <div className="slider-control-item">
                      <span className="slider-label">{t.memeTypeText}</span>
                      <input
                        type="text"
                        className="studio-text-input"
                        placeholder={t.memeTextPlaceholder}
                        value={o.text || ''}
                        onChange={(e) => update(o.id, { text: e.target.value })}
                        style={{ width: '100%', marginTop: '0.3rem' }}
                      />
                    </div>
                  ) : (
                    <div>
                      <input
                        ref={(el) => { fileInputRefs.current[o.id] = el; }}
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/gif,image/*"
                        style={{ display: 'none' }}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) uploadImage(o.id, file);
                          e.target.value = '';
                        }}
                      />
                      <label
                        className={`bgm-dropzone ${dragOverId === o.id ? 'drag-over' : ''}`}
                        style={{ display: 'block', cursor: 'pointer', textAlign: 'center' }}
                        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverId(o.id); }}
                        onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverId(null); }}
                        onDrop={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setDragOverId(null);
                          const file = e.dataTransfer.files?.[0];
                          if (file) uploadImage(o.id, file);
                        }}
                        onClick={() => fileInputRefs.current[o.id]?.click()}
                      >
                        <div className="dropzone-icon">
                          {uploadingId === o.id ? '⏳' : o.imageUrl ? '🖼️' : '📤'}
                        </div>
                        <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                          {uploadingId === o.id
                            ? '...'
                            : o.imageUrl ? t.memeChangeImage : (dragOverId === o.id ? t.memeDropActive : t.memeUploadMain)}
                        </div>
                        <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                          {t.memeUploadSub}
                        </div>
                      </label>
                      {o.imageUrl && (
                        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '0.5rem' }}>
                          <img src={o.imageUrl} alt="Meme preview" style={{ maxHeight: '90px', maxWidth: '100%', objectFit: 'contain' }} />
                        </div>
                      )}
                    </div>
                  )}

                  {/* Position presets */}
                  <div className="slider-quick-buttons" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                    <button type="button" onClick={() => preset(o.id, 50, 12)}>{t.memePresetTop}</button>
                    <button type="button" onClick={() => preset(o.id, 50, 50)}>{t.memePresetMiddle}</button>
                    <button type="button" onClick={() => preset(o.id, 50, 88)}>{t.memePresetBottom}</button>
                    <button type="button" onClick={() => preset(o.id, 15, 50)}>{t.memePresetLeft}</button>
                    <button type="button" onClick={() => preset(o.id, 50, 50)}>{t.memePresetCenterH}</button>
                    <button type="button" onClick={() => preset(o.id, 85, 50)}>{t.memePresetRight}</button>
                  </div>

                  {/* Position X */}
                  <div className="slider-control-item">
                    <div className="slider-label-row">
                      <span className="slider-label">{t.memeXLabel}</span>
                      <div className="slider-input-badge-wrap">
                        <input
                          type="number"
                          className="slider-number-input"
                          min={0}
                          max={100}
                          value={o.x}
                          onChange={(e) => update(o.id, { x: clamp(Number(e.target.value) || 0, 0, 100) })}
                        />
                        <span className="slider-input-unit">%</span>
                      </div>
                    </div>
                    <input
                      type="range"
                      className="studio-slider"
                      min="0"
                      max="100"
                      step="1"
                      value={o.x}
                      onChange={(e) => update(o.id, { x: Number(e.target.value) })}
                    />
                  </div>

                  {/* Position Y */}
                  <div className="slider-control-item">
                    <div className="slider-label-row">
                      <span className="slider-label">{t.memeYLabel}</span>
                      <div className="slider-input-badge-wrap">
                        <input
                          type="number"
                          className="slider-number-input"
                          min={0}
                          max={100}
                          value={o.y}
                          onChange={(e) => update(o.id, { y: clamp(Number(e.target.value) || 0, 0, 100) })}
                        />
                        <span className="slider-input-unit">%</span>
                      </div>
                    </div>
                    <input
                      type="range"
                      className="studio-slider"
                      min="0"
                      max="100"
                      step="1"
                      value={o.y}
                      onChange={(e) => update(o.id, { y: Number(e.target.value) })}
                    />
                  </div>

                  {/* Size */}
                  <div className="slider-control-item">
                    <div className="slider-label-row">
                      <span className="slider-label">{t.memeSizeLabel}</span>
                      <div className="slider-input-badge-wrap">
                        <input
                          type="number"
                          className="slider-number-input"
                          min={1}
                          max={100}
                          value={o.size}
                          onChange={(e) => update(o.id, { size: clamp(Number(e.target.value) || 1, 1, 100) })}
                        />
                        <span className="slider-input-unit">%</span>
                      </div>
                    </div>
                    <input
                      type="range"
                      className="studio-slider"
                      min="1"
                      max="100"
                      step="1"
                      value={o.size}
                      onChange={(e) => update(o.id, { size: Number(e.target.value) })}
                    />
                  </div>

                  {/* Opacity */}
                  <div className="slider-control-item">
                    <div className="slider-label-row">
                      <span className="slider-label">{t.memeOpacityLabel}</span>
                      <div className="slider-input-badge-wrap">
                        <input
                          type="number"
                          className="slider-number-input"
                          min={5}
                          max={100}
                          value={Math.round(o.opacity * 100)}
                          onChange={(e) => update(o.id, { opacity: clamp(Number(e.target.value) || 5, 5, 100) / 100 })}
                        />
                        <span className="slider-input-unit">%</span>
                      </div>
                    </div>
                    <input
                      type="range"
                      className="studio-slider"
                      min="5"
                      max="100"
                      step="5"
                      value={Math.round(o.opacity * 100)}
                      onChange={(e) => update(o.id, { opacity: Number(e.target.value) / 100 })}
                    />
                  </div>

                  {/* Rotation (image only) */}
                  {o.type === 'image' && (
                    <div className="slider-control-item">
                      <div className="slider-label-row">
                        <span className="slider-label">{t.memeRotationLabel}</span>
                        <div className="slider-input-badge-wrap">
                          <input
                            type="number"
                            className="slider-number-input"
                            min={-180}
                            max={180}
                            value={o.rotation || 0}
                            onChange={(e) => update(o.id, { rotation: clamp(Number(e.target.value) || 0, -180, 180) })}
                          />
                          <span className="slider-input-unit">°</span>
                        </div>
                      </div>
                      <input
                        type="range"
                        className="studio-slider"
                        min="-180"
                        max="180"
                        step="5"
                        value={o.rotation || 0}
                        onChange={(e) => update(o.id, { rotation: Number(e.target.value) })}
                      />
                    </div>
                  )}

                  {/* Text colors */}
                  {o.type === 'text' && (
                    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                      <div className="slider-control-item" style={{ flex: '1 1 8rem' }}>
                        <span className="slider-label">{t.memeTextColorLabel}</span>
                        <input
                          type="color"
                          value={o.fontColor || '#ffffff'}
                          onChange={(e) => update(o.id, { fontColor: e.target.value })}
                          style={{ width: '100%', height: '32px', marginTop: '0.3rem', cursor: 'pointer', background: 'transparent', border: 'none' }}
                        />
                      </div>
                      <div className="slider-control-item" style={{ flex: '1 1 8rem' }}>
                        <span className="slider-label">{t.memeOutlineColorLabel}</span>
                        <input
                          type="color"
                          value={o.outlineColor || '#000000'}
                          onChange={(e) => update(o.id, { outlineColor: e.target.value })}
                          style={{ width: '100%', height: '32px', marginTop: '0.3rem', cursor: 'pointer', background: 'transparent', border: 'none' }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Timing */}
                  <div style={{ display: 'flex', gap: '0.8rem', flexWrap: 'wrap' }}>
                    <div className="slider-control-item" style={{ flex: '1 1 7rem' }}>
                      <span className="slider-label">{t.memeStartLabel}</span>
                      <input
                        type="number"
                        className="slider-number-input"
                        min={0}
                        max={clipDuration || undefined}
                        step={0.5}
                        value={o.startTime}
                        onChange={(e) => update(o.id, { startTime: Math.max(0, Number(e.target.value) || 0) })}
                        style={{ width: '100%', marginTop: '0.3rem' }}
                      />
                    </div>
                    <div className="slider-control-item" style={{ flex: '1 1 7rem' }}>
                      <span className="slider-label">{t.memeEndLabel}</span>
                      <input
                        type="number"
                        className="slider-number-input"
                        min={0}
                        max={clipDuration || undefined}
                        step={0.5}
                        placeholder="∞"
                        value={o.endTime ?? ''}
                        onChange={(e) => {
                          const raw = e.target.value;
                          update(o.id, { endTime: raw === '' ? null : Math.max(0, Number(raw) || 0) });
                        }}
                        style={{ width: '100%', marginTop: '0.3rem' }}
                      />
                    </div>
                  </div>
                  <p className="bgm-hint-text" style={{ margin: 0 }}>{t.memeDurationHint}</p>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default MemeOverlayEditor;
