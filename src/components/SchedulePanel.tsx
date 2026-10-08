import React, { useEffect, useMemo, useState } from 'react';
import { useLanguage } from '../locales';

interface PlatformInfo {
  key: string;
  label: string;
  safe_per_day: number;
  max_per_day: number;
  gap_hours: number;
  best_hours: number[];
  note: string;
}

interface Recommendation {
  platform: string;
  platform_label: string;
  platform_note: string;
  clip_count: number;
  recommended_per_day: number;
  recommended_per_day_source: string;
  safe_per_day: number;
  max_per_day: number;
  recommended_per_hour: number;
  gap_hours: number;
  ideal_gap_hours: number[];
  best_hours: number[];
  days_needed: number;
  warnings: string[];
}

interface ScheduleEntry {
  index: number;
  date: string;
  time: string;
  datetime: string;
  weekday: string;
  day_number: number;
  title?: string | null;
}

interface ScheduleResult {
  recommendation: Recommendation;
  schedule: ScheduleEntry[];
  start_date: string;
  lang: string;
}

interface SchedulePanelProps {
  clipCount: number;
  clipTitles?: string[];
}

const T = {
  id: {
    title: '⏰ Jadwal Jam Tayang',
    subtitle: 'Atur kapan tiap klip di-upload supaya aman & tidak kena shadowban.',
    platform: 'Platform',
    clipsPerDay: 'Berapa kali per hari',
    clipsPerHour: 'Maks per jam',
    auto: 'Otomatis (aman)',
    startDate: 'Mulai tanggal',
    planBtn: '📅 Buat Jadwal',
    planning: 'Menghitung…',
    summary: 'Rekomendasi',
    perDay: 'unggahan / hari',
    perHour: 'unggahan / jam',
    gap: 'jarak ideal',
    hours: 'jam',
    bestHours: 'Jam terbaik',
    daysNeeded: 'Total hari',
    days: 'hari',
    timetable: 'Tabel Jam Tayang',
    copyBtn: '📋 Salin Jadwal',
    copied: '✅ Tersalin!',
    csvBtn: '⬇️ CSV',
    empty: 'Belum ada jadwal. Klik "Buat Jadwal".',
    clips: 'klip',
    clip: 'Klip',
    date: 'Tanggal',
    day: 'Hari',
    time: 'Jam',
    title_col: 'Judul',
    warnTitle: '⚠️ Perhatian',
  },
  en: {
    title: '⏰ Upload Schedule',
    subtitle: 'Plan when each clip goes live — safe cadence, no shadowban.',
    platform: 'Platform',
    clipsPerDay: 'How many per day',
    clipsPerHour: 'Max per hour',
    auto: 'Auto (safe)',
    startDate: 'Start date',
    planBtn: '📅 Build Schedule',
    planning: 'Calculating…',
    summary: 'Recommendation',
    perDay: 'uploads / day',
    perHour: 'uploads / hour',
    gap: 'ideal gap',
    hours: 'hours',
    bestHours: 'Best hours',
    daysNeeded: 'Total days',
    days: 'days',
    timetable: 'Publish Timetable',
    copyBtn: '📋 Copy Schedule',
    copied: '✅ Copied!',
    csvBtn: '⬇️ CSV',
    empty: 'No schedule yet. Click "Build Schedule".',
    clips: 'clips',
    clip: 'Clip',
    date: 'Date',
    day: 'Day',
    time: 'Time',
    title_col: 'Title',
    warnTitle: '⚠️ Notice',
  },
};

const SchedulePanel: React.FC<SchedulePanelProps> = ({ clipCount, clipTitles }) => {
  const { language } = useLanguage();
  const t = T[language === 'id' ? 'id' : 'en'];

  const [platforms, setPlatforms] = useState<PlatformInfo[]>([]);
  const [platform, setPlatform] = useState('youtube_shorts');
  const [perDay, setPerDay] = useState<string>('');
  const [startDate, setStartDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [result, setResult] = useState<ScheduleResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch('/api/schedule/platforms')
      .then((r) => r.json())
      .then((data) => {
        if (!alive) return;
        if (data?.platforms) setPlatforms(data.platforms);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const currentPlatform = useMemo(
    () => platforms.find((p) => p.key === platform),
    [platforms, platform]
  );

  const buildSchedule = async () => {
    setLoading(true);
    setError('');
    try {
      const resp = await fetch('/api/schedule/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clip_count: Math.max(1, clipCount || 1),
          platform,
          clips_per_day: perDay ? parseInt(perDay, 10) : null,
          start_date: startDate,
          lang: language,
          clip_titles: clipTitles && clipTitles.length ? clipTitles : null,
        }),
      });
      if (!resp.ok) throw new Error('Request failed');
      const data: ScheduleResult = await resp.json();
      setResult(data);
    } catch (e: any) {
      setError(language === 'id' ? 'Gagal menghitung jadwal.' : 'Failed to build schedule.');
    } finally {
      setLoading(false);
    }
  };

  const copySchedule = async () => {
    if (!result) return;
    const lines = result.schedule.map(
      (e) =>
        `${e.index}. ${e.date} (${e.weekday}) ${e.time}` +
        (e.title ? ` — ${e.title}` : '')
    );
    const text =
      `${t.timetable} (${result.recommendation.platform_label})\n` +
      `${t.perDay}: ${result.recommendation.recommended_per_day} | ${t.perHour}: ${result.recommendation.recommended_per_hour}\n\n` +
      lines.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard may be blocked */
    }
  };

  const downloadCsv = () => {
    if (!result) return;
    const header = `${t.clip},${t.date},${t.day},${t.time},${t.title_col}\n`;
    const rows = result.schedule
      .map(
        (e) =>
          `${e.index},${e.date},${e.weekday},${e.time},"${(e.title || '').replace(/"/g, '""')}"`
      )
      .join('\n');
    const blob = new Blob([header + rows], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `jadwal_tayang_${result.start_date}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const rec = result?.recommendation;

  return (
    <div className="schedule-panel-card">
      <div className="schedule-panel-head">
        <div>
          <h4 className="schedule-panel-title">{t.title}</h4>
          <p className="schedule-panel-sub">{t.subtitle}</p>
        </div>
        <span className="schedule-clip-count-badge">
          {Math.max(1, clipCount || 1)} {t.clips}
        </span>
      </div>

      <div className="schedule-controls">
        <div className="schedule-field">
          <label>{t.platform}</label>
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {platforms.length === 0 && <option value="youtube_shorts">YouTube Shorts</option>}
            {platforms.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        <div className="schedule-field">
          <label>{t.clipsPerDay}</label>
          <select value={perDay} onChange={(e) => setPerDay(e.target.value)}>
            <option value="">{t.auto}</option>
            {[1, 2, 3, 4, 5, 6, 8, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>

        <div className="schedule-field">
          <label>{t.startDate}</label>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </div>

        <button
          type="button"
          className="schedule-plan-btn"
          onClick={buildSchedule}
          disabled={loading}
        >
          {loading ? t.planning : t.planBtn}
        </button>
      </div>

      {currentPlatform && (
        <div className="schedule-platform-note">
          💡 {currentPlatform.note}
        </div>
      )}

      {error && <div className="schedule-error">❌ {error}</div>}

      {rec && (
        <>
          <div className="schedule-summary">
            <div className="schedule-stat">
              <span className="stat-val">{rec.recommended_per_day}</span>
              <span className="stat-label">{t.perDay}</span>
            </div>
            <div className="schedule-stat highlight">
              <span className="stat-val">{rec.recommended_per_hour}</span>
              <span className="stat-label">{t.perHour}</span>
            </div>
            <div className="schedule-stat">
              <span className="stat-val">
                {rec.ideal_gap_hours[0]}-{rec.ideal_gap_hours[1]}
              </span>
              <span className="stat-label">
                {t.gap} ({t.hours})
              </span>
            </div>
            <div className="schedule-stat">
              <span className="stat-val">{rec.best_hours.join(':00, ')}:00</span>
              <span className="stat-label">{t.bestHours}</span>
            </div>
            <div className="schedule-stat">
              <span className="stat-val">{rec.days_needed}</span>
              <span className="stat-label">{t.daysNeeded} ({t.days})</span>
            </div>
          </div>

          {rec.warnings && rec.warnings.length > 0 && (
            <div className="schedule-warnings">
              <strong>{t.warnTitle}</strong>
              <ul>
                {rec.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {result && result.schedule.length > 0 && (
        <div className="schedule-table-wrap">
          <div className="schedule-table-head">
            <span className="schedule-table-title">{t.timetable}</span>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button type="button" className="schedule-mini-btn" onClick={copySchedule}>
                {copied ? t.copied : t.copyBtn}
              </button>
              <button type="button" className="schedule-mini-btn" onClick={downloadCsv}>
                {t.csvBtn}
              </button>
            </div>
          </div>
          <div className="schedule-table-scroll">
            <table className="schedule-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t.date}</th>
                  <th>{t.day}</th>
                  <th>{t.time}</th>
                  <th>{t.title_col}</th>
                </tr>
              </thead>
              <tbody>
                {result.schedule.map((e) => (
                  <tr key={e.index}>
                    <td className="sched-idx">{e.index}</td>
                    <td>{e.date}</td>
                    <td>{e.weekday}</td>
                    <td className="sched-time">{e.time}</td>
                    <td className="sched-title" title={e.title || ''}>
                      {e.title || `—`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {result && result.schedule.length === 0 && (
        <div className="schedule-empty">{t.empty}</div>
      )}
    </div>
  );
};

export default SchedulePanel;
