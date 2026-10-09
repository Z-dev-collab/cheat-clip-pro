import React, { useCallback, useEffect, useState } from 'react';
import { useLanguage } from '../locales';
import type { RenderHistoryEntry } from '../types';

interface RenderHistoryPanelProps {
  /** Bumping this value (e.g. after a render finishes) triggers a refresh. */
  refreshKey?: number;
  /** Called when the user loads a past render's clips back into the studio. */
  onRestoreClips?: (entry: RenderHistoryEntry) => void;
}

const formatTimestamp = (iso: string): string => {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
};

const RenderHistoryPanel: React.FC<RenderHistoryPanelProps> = ({ refreshKey, onRestoreClips }) => {
  const { t } = useLanguage();
  const [entries, setEntries] = useState<RenderHistoryEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isClearing, setIsClearing] = useState(false);

  const loadHistory = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch('/api/render-history');
      if (res.ok) {
        const data = await res.json();
        setEntries(Array.isArray(data.entries) ? data.entries : []);
      }
    } catch (e) {
      console.warn('Failed to load render history:', e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory, refreshKey]);

  const handleDelete = async (batchId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const res = await fetch(`/api/render-history/${encodeURIComponent(batchId)}`, { method: 'DELETE' });
      if (res.ok) {
        setEntries(prev => prev.filter(en => en.batch_id !== batchId));
        if (expandedId === batchId) setExpandedId(null);
      }
    } catch (err) {
      console.warn('Failed to delete render history entry:', err);
    }
  };

  const handleClearAll = async () => {
    if (isClearing) return;
    setIsClearing(true);
    try {
      const res = await fetch('/api/render-history', { method: 'DELETE' });
      if (res.ok) {
        setEntries([]);
        setExpandedId(null);
      }
    } catch (err) {
      console.warn('Failed to clear render history:', err);
    } finally {
      setIsClearing(false);
    }
  };

  const statusLabel = (entry: RenderHistoryEntry): string => {
    if (entry.overall_status === 'error') return t.studio.renderHistoryStatusFailed;
    if ((entry.failed_count || 0) > 0) {
      return t.studio.renderHistoryStatusPartial(entry.completed_count || 0, entry.total_clips || 0);
    }
    return t.studio.renderHistoryStatusDone(entry.completed_count || 0);
  };

  return (
    <div className="studio-card-group render-history-group">
      <div className="group-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '0.5rem' }}>
        <span className="group-title">{t.studio.renderHistoryTitle}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <span className="group-badge">
            {t.studio.renderHistoryCount(entries.length)}
          </span>
          <button
            type="button"
            className="render-history-refresh-btn"
            onClick={loadHistory}
            disabled={isLoading}
            title={t.studio.renderHistoryRefresh}
          >
            {isLoading ? '⏳' : '↻'}
          </button>
          {entries.length > 0 && (
            <button
              type="button"
              className="render-history-clear-btn"
              onClick={handleClearAll}
              disabled={isClearing}
              title={t.studio.renderHistoryClearAll}
            >
              🗑
            </button>
          )}
        </div>
      </div>

      <p className="render-history-desc">{t.studio.renderHistoryDesc}</p>

      {entries.length === 0 ? (
        <div className="batch-titles-empty-box">
          <span>🕘</span>
          <span>{isLoading ? t.studio.renderHistoryLoading : t.studio.renderHistoryEmpty}</span>
        </div>
      ) : (
        <div className="render-history-list">
          {entries.map(entry => {
            const isExpanded = expandedId === entry.batch_id;
            const clips = entry.clips || [];
            const completedClips = clips.filter(c => c.status === 'completed');
            return (
              <div key={entry.batch_id} className="render-history-card">
                <div
                  className="render-history-card-head"
                  onClick={() => setExpandedId(isExpanded ? null : entry.batch_id)}
                >
                  <div className="render-history-head-main">
                    <span className="render-history-status-pill">{statusLabel(entry)}</span>
                    <span className="render-history-date">{formatTimestamp(entry.created_at)}</span>
                    {entry.is_merged && <span className="render-history-merged-pill">{t.studio.renderHistoryMerged}</span>}
                  </div>
                  <div className="render-history-head-actions">
                    <span className="render-history-chevron">{isExpanded ? '▲' : '▼'}</span>
                    <button
                      type="button"
                      className="render-history-delete-btn"
                      onClick={(e) => handleDelete(entry.batch_id, e)}
                      title={t.studio.renderHistoryDelete}
                    >
                      ✕
                    </button>
                  </div>
                </div>

                <div className="render-history-meta-row">
                  <span>{t.studio.renderHistoryClipsMeta(entry.completed_count || 0, entry.total_clips || 0)}</span>
                  {entry.settings?.aspect_ratio && <span>· {entry.settings.aspect_ratio}</span>}
                  {entry.settings?.render_mode && <span>· {entry.settings.render_mode}</span>}
                </div>

                {isExpanded && (
                  <div className="render-history-details">
                    {completedClips.length > 0 ? (
                      <div className="recent-items-scroll">
                        {completedClips.map((c, i) => (
                          <div key={i} className="recent-file-row">
                            <span className="file-idx">#{c.clip_index != null ? c.clip_index + 1 : i + 1}</span>
                            <span className="file-name" title={c.title || ''}>{c.title || `clip_${i + 1}`}</span>
                            {c.duration != null && <span className="render-history-dur">{Math.round(c.duration)}s</span>}
                            {c.download_url && (
                              <a
                                href={c.download_url}
                                download
                                className="quick-dl-btn"
                                onClick={(e) => e.stopPropagation()}
                                title={t.studio.renderHistoryDownloadClip}
                              >
                                ⬇️ MP4
                              </a>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="render-history-empty-detail">{t.studio.renderHistoryNoClips}</div>
                    )}

                    <div className="render-history-detail-actions">
                      {entry.zip_url && (
                        <a
                          href={entry.zip_url}
                          download
                          className="render-history-action-btn"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {t.studio.renderHistoryDownloadZip}
                        </a>
                      )}
                      {onRestoreClips && clips.length > 0 && (
                        <button
                          type="button"
                          className="render-history-action-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            onRestoreClips(entry);
                          }}
                        >
                          {t.studio.renderHistoryRestore}
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default RenderHistoryPanel;
