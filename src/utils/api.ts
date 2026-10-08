/**
 * Resilient API client for Cheat Clip Pro.
 * Handles automatic retry when backend server is starting up or reloading (503 / network refused).
 */

export interface ResilientFetchOptions extends RequestInit {
  maxRetries?: number;
  retryDelay?: number;
  silent?: boolean;
}

/**
 * Fetch wrapper with automatic retry on 502/503/504 or network connection errors.
 * Ideal for startup probes (/api/cookies, /api/hardware-accel, /api/models).
 */
export async function resilientFetch(
  input: RequestInfo | URL,
  options: ResilientFetchOptions = {}
): Promise<Response> {
  const {
    maxRetries = 4,
    retryDelay = 800,
    silent = false,
    ...fetchInit
  } = options;

  let lastError: any = null;
  let lastResponse: Response | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(input, fetchInit);

      // If backend is ready (status < 500 or not 502/503/504), return response
      if (response.status !== 503 && response.status !== 502 && response.status !== 504) {
        return response;
      }

      lastResponse = response;
      if (!silent) {
        console.warn(
          `[resilientFetch] Backend responded with HTTP ${response.status} for ${String(input)} (attempt ${attempt + 1}/${maxRetries + 1}). Retrying in ${retryDelay * Math.pow(1.5, attempt)}ms...`
        );
      }
    } catch (err: any) {
      lastError = err;
      if (!silent) {
        console.warn(
          `[resilientFetch] Network error for ${String(input)} (attempt ${attempt + 1}/${maxRetries + 1}):`,
          err?.message || err
        );
      }
    }

    if (attempt < maxRetries) {
      const delay = Math.round(retryDelay * Math.pow(1.5, attempt));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  if (lastResponse) {
    return lastResponse;
  }
  throw lastError || new Error(`Failed to fetch ${String(input)} after ${maxRetries + 1} attempts`);
}

// --------------------------------------------------------------------------- //
//  Live broadcast helpers
// --------------------------------------------------------------------------- //

export interface LiveProbeResult {
  ok: boolean;
  error?: string;
  title?: string;
  channel?: string;
  duration?: number;
  is_live?: boolean;
  live_status?: string;
  was_live?: boolean;
  thumbnail?: string;
  webpage_url?: string;
}

export interface LiveRecordStatus {
  job_id: string;
  status: 'starting' | 'recording' | 'finishing' | 'stopping' | 'ready' | 'stopped' | 'failed';
  title?: string;
  from_start?: boolean;
  elapsed?: number;
  downloaded_bytes?: number;
  total_bytes?: number;
  speed?: number;
  filename?: string;
  file_path?: string;
  video_url?: string;
  video_id?: string;
  size_bytes?: number;
  error?: string;
}

/** Checks whether a pasted link is a live broadcast. Never throws — returns ok:false on failure. */
export async function probeLive(url: string): Promise<LiveProbeResult> {
  try {
    const res = await fetch('/api/live/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      return { ok: false, error: j.detail || `HTTP ${res.status}` };
    }
    return await res.json();
  } catch (err: any) {
    return { ok: false, error: err?.message || 'Network error' };
  }
}

/** Starts a background recording of a live broadcast. */
export async function startLiveRecord(
  url: string,
  fromStart: boolean = false,
  title?: string
): Promise<{ job_id: string; status: string; filename: string }> {
  const res = await fetch('/api/live/record/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, from_start: fromStart, title }),
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j.detail || `Failed to start recording (HTTP ${res.status})`);
  }
  return await res.json();
}

/** Polls the status of a running recording job. */
export async function getLiveRecordStatus(jobId: string): Promise<LiveRecordStatus> {
  const res = await fetch(`/api/live/record/status/${jobId}`);
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j.detail || `HTTP ${res.status}`);
  }
  return await res.json();
}

/** Stops a running recording; the backend finalizes the file. */
export async function stopLiveRecord(jobId: string): Promise<LiveRecordStatus> {
  const res = await fetch(`/api/live/record/stop/${jobId}`, { method: 'POST' });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j.detail || `HTTP ${res.status}`);
  }
  return await res.json();
}
