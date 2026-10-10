from typing import List, Optional, Dict, Any
from pydantic import BaseModel

class MemeOverlayModel(BaseModel):
    """A single meme overlay (sticker/text) placed on the clip canvas."""
    type: str = "text"                 # "text" | "image"
    text: Optional[str] = None         # for type=text
    image_path: Optional[str] = None   # for type=image (server path or uploads filename)
    x: float = 50.0                    # center X anchor (percent of canvas width)
    y: float = 50.0                    # center Y anchor (percent of canvas height)
    size: float = 40.0                 # text: font size % of width; image: width % of canvas
    start_time: Optional[float] = 0.0  # seconds into the clip
    end_time: Optional[float] = None   # seconds into the clip (None = until end)
    opacity: float = 1.0               # 0.0 - 1.0
    font_color: Optional[str] = "white"
    outline_color: Optional[str] = "black"
    font: Optional[str] = None
    rotation: Optional[float] = 0.0    # degrees (image only)


class RenderSettingsModel(BaseModel):
    aspect_ratio: str = "9:16"
    background_style: str = "black"
    enable_face_tracking: bool = True
    streamer_preset: str = "none"
    facecam_position: Optional[str] = "auto"
    title_text: Optional[str] = None
    title_prefix: Optional[str] = ""
    title_suffix: Optional[str] = ""
    file_name_prefix: Optional[str] = ""
    file_name_suffix: Optional[str] = ""
    title_position: str = "auto"
    title_duration: Optional[str] = "entire"
    subtitles_enabled: Optional[bool] = True
    caption_style: str = "viral_pop"
    caption_font: str = "Outfit"
    title_font: Optional[str] = "Outfit"
    font_size: str = "medium"
    title_font_size: Optional[str] = "medium"
    font_size_px: Optional[int] = None
    title_font_size_px: Optional[int] = None
    text_case: str = "uppercase"
    title_text_case: Optional[str] = "uppercase"
    title_y_percent: Optional[float] = None
    subtitle_y_percent: Optional[float] = None
    subtitle_position_mode: Optional[str] = "bottom"
    subtitle_center_y_percent: Optional[float] = 50.0
    # Background Music
    bgm_enabled: Optional[bool] = False
    bgm_file_path: Optional[str] = None
    bgm_volume: Optional[float] = 25.0
    bgm_start_offset: Optional[float] = 0.0
    # Hook SFX
    hook_sfx_enabled: Optional[bool] = False
    hook_sfx_file_path: Optional[str] = None
    hook_sfx_volume: Optional[float] = 100.0
    # Raw Audio / Voice Boost
    original_audio_volume: Optional[float] = 100.0
    # Watermark
    watermark_enabled: Optional[bool] = False
    watermark_type: Optional[str] = "image"
    watermark_file_path: Optional[str] = None
    watermark_text: Optional[str] = None
    watermark_size: Optional[float] = 20.0
    watermark_opacity: Optional[float] = 80.0
    watermark_x: Optional[float] = 90.0
    watermark_y: Optional[float] = 8.0
    # Meme Overlays (interactive editor)
    meme_enabled: Optional[bool] = False
    meme_overlays: Optional[List[MemeOverlayModel]] = None
    hardware_accel: Optional[str] = "auto"
    # Multi-Segment Merged Highlight Video
    render_mode: Optional[str] = "separate"  # "separate" | "merged"
    compilation_title: Optional[str] = None
    # Auto Cover / Thumbnail
    cover_enabled: Optional[bool] = False
    # "Jedag jedug" beat-synced auto edit (gameplay gaming)
    beat_punch_enabled: Optional[bool] = False
    beat_intensity: Optional[float] = 0.16
    beat_shake: Optional[bool] = True
    beat_flash: Optional[bool] = True

class RenderBatchRequest(BaseModel):
    video_url: str
    video_id: str
    clips: List[Dict[str, Any]]
    settings: RenderSettingsModel
    transcript: Optional[List[Dict[str, Any]]] = None

class RetryBatchRequest(BaseModel):
    clip_indices: Optional[List[int]] = None
