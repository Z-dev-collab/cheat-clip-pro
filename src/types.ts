export interface HeatmapPoint {
  start_time: number;
  end_time: number;
  value: number;
}

export interface TranscriptLine {
  start: number;
  end: number;
  text: string;
  engagement?: number;
}

export interface ViralClip {
  title: string;
  start_time: number;
  end_time: number;
  hook_time?: number;
  virality_score: number;
  key_quotes: string[];
  transcript: string;
  title_suggestion?: string;
  caption_suggestion?: string;
  hashtag_suggestion?: string;
}

export interface AnalyzeResponse {
  video_id: string;
  title: string;
  duration: number;
  heatmap: HeatmapPoint[];
  summary: string;
  clips: ViralClip[];
  transcript?: TranscriptLine[];
  model?: string;
  video_url?: string;
  source_type?: 'youtube' | 'upload' | 'gdrive' | 'site';
}

export type AspectRatioOption = '9:16' | '1:1' | '4:3' | '16:9' | '16:9_landscape';
export type BackgroundStyle = 'black' | 'blurred';
export type CaptionStyle = 
  | 'viral_pop' 
  | 'beast_punch' 
  | 'cyber_violet' 
  | 'fire_red' 
  | 'electric_cyan' 
  | 'golden_aura' 
  | 'clean_minimal' 
  | 'none';
export type CaptionFont = 
  | 'Outfit' 
  | 'Montserrat' 
  | 'Inter' 
  | 'Impact' 
  | 'Bebas Neue' 
  | 'Anton' 
  | 'Poppins' 
  | 'Arial Black'
  | (string & {});
export type TitlePosition = 'auto' | 'safe_zone' | 'middle' | 'none';
export type TitleDurationOption = 'entire' | '5s' | '10s';
export type SubtitlePositionMode = 'bottom' | 'center';
export type StreamerPreset = 'none' | 'split_top_cam' | 'pip_corner';
export type FacecamPosition = 'auto' | 'bottom_right' | 'top_right' | 'bottom_left' | 'top_left' | 'center' | 'left' | 'right';
export type FontSizeOption = 'small' | 'medium' | 'big' | 'custom';
export type TextCaseOption = 'uppercase' | 'capitalize' | 'lowercase';
export type HardwareAccelOption = 'auto' | 'nvenc' | 'amf' | 'qsv' | 'cpu';

export interface FontItem {
  name: string;
  is_custom: boolean;
  filename?: string | null;
  url?: string | null;
}

export interface HardwareAccelInfo {
  status: string;
  active_default: string;
  recommended: string;
  support: {
    nvenc: boolean;
    amf: boolean;
    qsv: boolean;
    cpu: boolean;
    recommended: string;
  };
  options: Array<{
    id: HardwareAccelOption;
    label: string;
    sub: string;
    available: boolean;
  }>;
}

export interface MemeOverlay {
  id: string;
  type: 'text' | 'image';
  text?: string;
  imagePath?: string;
  imageUrl?: string;
  x: number;        // 0 to 100 (% of canvas width, center anchor)
  y: number;        // 0 to 100 (% of canvas height, center anchor)
  size: number;     // text: font size % of height; image: width % of canvas
  startTime: number; // seconds into the clip
  endTime?: number | null; // seconds into the clip (null = until end)
  opacity: number;  // 0 to 1
  fontColor?: string;
  outlineColor?: string;
  font?: string;
  rotation?: number; // degrees (image only)
}

export interface RenderSettings {
  aspectRatio: AspectRatioOption;
  backgroundStyle: BackgroundStyle;
  enableFaceTracking: boolean;
  streamerPreset: StreamerPreset;
  facecamPosition?: FacecamPosition;
  titleText?: string;
  titlePrefix?: string;
  titleSuffix?: string;
  fileNamePrefix?: string;
  fileNameSuffix?: string;
  titlePosition: TitlePosition;
  titleDuration?: TitleDurationOption;
  titleFont?: CaptionFont;
  titleFontSize?: FontSizeOption;
  titleFontSizePx?: number;
  titleTextCase?: TextCaseOption;
  subtitlesEnabled?: boolean;
  captionStyle: CaptionStyle;
  captionFont: CaptionFont;
  fontSize: FontSizeOption;
  fontSizePx?: number;
  textCase: TextCaseOption;
  titleYPercent?: number;
  subtitleYPercent?: number;
  subtitlePositionMode?: SubtitlePositionMode;
  subtitleCenterYPercent?: number;
  selectedClips: ViralClip[];
  // Background Music
  bgmEnabled?: boolean;
  bgmFilePath?: string;
  bgmFileName?: string;
  bgmVolume?: number; // 0 to 100%
  bgmStartOffset?: number; // seconds from start of audio track
  // Hook SFX
  hookSfxEnabled?: boolean;
  hookSfxFilePath?: string;
  hookSfxFileName?: string;
  hookSfxVolume?: number; // 0 to 150%
  // Raw Audio / Voice Boost
  originalAudioVolume?: number; // 0 to 200%
  // Watermark
  watermarkEnabled?: boolean;
  watermarkType?: 'image' | 'text';
  watermarkFilePath?: string;
  watermarkUrl?: string;
  watermarkText?: string;
  watermarkSize?: number; // 5 to 50%
  watermarkOpacity?: number; // 10 to 100%
  watermarkX?: number; // 0 to 100%
  watermarkY?: number; // 0 to 100%
  // Meme Overlays (interactive editor)
  memeEnabled?: boolean;
  memeOverlays?: MemeOverlay[];
  // Hardware Acceleration / Video Encoder
  hardwareAccel?: HardwareAccelOption;
  // Multi-Segment Merged Highlight Video
  renderMode?: 'separate' | 'merged';
  compilationTitle?: string;
  // Auto Cover / Thumbnail
  coverEnabled?: boolean;
}

export interface RenderClipStatus {
  clip_index: number;
  title: string;
  base_title?: string;
  status: 'pending' | 'downloading' | 'transcribing' | 'tracking' | 'rendering' | 'completed' | 'error';
  progress_percent: number;
  download_url?: string;
  cover_url?: string;
  cover_filename?: string;
  error_message?: string;
  error?: string;
}

export interface BatchRenderProgress {
  batch_id: string;
  total_clips: number;
  current_clip_index: number;
  overall_status: 'running' | 'completed' | 'error';
  zip_url?: string;
  error_message?: string;
  warning_message?: string;
  clips: RenderClipStatus[];
}

export interface RenderHistoryClipRecord {
  clip_index?: number | null;
  title?: string | null;
  base_title?: string | null;
  status?: string | null;
  download_url?: string | null;
  cover_url?: string | null;
  start_time?: number | null;
  end_time?: number | null;
  duration?: number | null;
}

export interface RenderHistoryEntry {
  batch_id: string;
  created_at: string;
  overall_status?: string | null;
  is_merged?: boolean;
  total_clips?: number;
  completed_count?: number;
  failed_count?: number;
  zip_url?: string | null;
  video_id?: string | null;
  video_url?: string | null;
  settings?: {
    aspect_ratio?: string | null;
    caption_style?: string | null;
    render_mode?: string | null;
    cover_enabled?: boolean;
  } | null;
  clips?: RenderHistoryClipRecord[];
}
