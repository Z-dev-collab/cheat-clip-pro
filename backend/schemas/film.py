from typing import List, Optional

from pydantic import BaseModel, Field


class FilmSearchRequest(BaseModel):
    query: str = Field(..., description="Judul film yang dicari (mis. 'dracula 1931')")
    limit: int = Field(12, description="Maksimum hasil")


class FilmResult(BaseModel):
    identifier: str = ""
    title: str = ""
    year: Optional[str] = ""
    description: Optional[str] = ""
    downloads: Optional[object] = None
    item_size: Optional[str] = ""
    poster: Optional[str] = ""
    url: Optional[str] = ""


class FilmSearchResponse(BaseModel):
    query: str
    count: int
    source: str = "archive.org"
    results: List[FilmResult] = Field(default_factory=list)


class FilmPart(BaseModel):
    file: str = ""
    label: str = ""
    format: str = ""
    size: Optional[str] = ""
    size_bytes: Optional[int] = 0
    length: Optional[float] = 0.0
    duration: Optional[str] = ""
    download_url: str = ""
    preview_url: str = ""


class FilmPartsResponse(BaseModel):
    identifier: str
    title: str = ""
    year: Optional[str] = ""
    description: Optional[str] = ""
    creator: Optional[str] = ""
    licenseurl: Optional[str] = ""
    poster: str = ""
    url: Optional[str] = ""
    parts: List[FilmPart] = Field(default_factory=list)


class FilmDownloadRequest(BaseModel):
    identifier: str = Field(..., description="Identifier item Archive.org")
    file: str = Field(..., description="Nama file video (part) yang dipilih")
    title_hint: str = Field("", description="Judul untuk penamaan file")


# ── Film segmentation (trailer + 60s parts) ──────────────────────────────────

class FilmSegmentsRequest(BaseModel):
    duration: float = Field(..., description="Durasi film (detik)")
    part_seconds: float = Field(150.0, description="Panjang tiap part (detik), default 150 (2 menit 30 detik)")
    trailer_seconds: float = Field(60.0, description="Panjang trailer (detik), default 60")
    include_trailer: bool = Field(True, description="Sertakan potongan trailer")
    video_url: Optional[str] = Field(None, description="URL/path video lokal untuk analisis heatmap (opsional)")


class FilmSegment(BaseModel):
    index: int = 0
    label: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    is_trailer: bool = False


class FilmSegmentsResponse(BaseModel):
    duration: float = 0.0
    part_seconds: float = 150.0
    part_count: int = 0
    total_parts_duration: float = 0.0
    trailer: Optional[FilmSegment] = None
    parts: List[FilmSegment] = Field(default_factory=list)


# ── Background-music recommendation ─────────────────────────────────────────

class BgmRecommendRequest(BaseModel):
    mood: str = Field("epic", description="Mood/suasana (epic, action, tense, sad, calm, happy, romantic, mysterious)")
    query: Optional[str] = Field("", description="Kata kunci bebas (opsional, menimpa mood)")
    limit: int = Field(6, description="Jumlah rekomendasi")


class BgmTrack(BaseModel):
    identifier: str = ""
    title: str = ""
    creator: Optional[str] = ""
    year: Optional[str] = ""
    licenseurl: Optional[str] = ""
    downloads: Optional[object] = None
    size: Optional[str] = ""
    size_bytes: Optional[int] = 0
    length: Optional[float] = 0.0
    duration: Optional[str] = ""
    audio_file: Optional[str] = ""
    audio_format: Optional[str] = ""
    download_url: Optional[str] = ""
    preview_url: Optional[str] = ""
    poster: Optional[str] = ""
    url: Optional[str] = ""
    mood: Optional[str] = ""


class BgmRecommendResponse(BaseModel):
    mood: str = "epic"
    query: str = ""
    count: int = 0
    source: str = "archive.org"
    tracks: List[BgmTrack] = Field(default_factory=list)


class BgmDownloadRequest(BaseModel):
    identifier: str = Field(..., description="Identifier item audio Archive.org")
    file: str = Field(..., description="Nama file audio yang dipilih")
    title_hint: str = Field("", description="Judul untuk penamaan file")


# ── Per-part caption recommendation ─────────────────────────────────────────

class FilmCaptionPartInput(BaseModel):
    index: int = Field(0, description="Indeks part (mulai 0)")
    label: str = Field("", description="Label part, mis. 'Part 1'")


class FilmCaptionsRequest(BaseModel):
    title: str = Field(..., description="Judul film")
    parts: List[FilmCaptionPartInput] = Field(default_factory=list, description="Daftar part yang mau dibuatkan caption")
    language: str = Field("", description="Nama bahasa target (mis. 'Indonesia')")
    provider: str = Field("gemini", description="Provider LLM: 'gemini' atau 'openai' (9router/openai-compatible)")
    base_url: str = Field("", description="Base URL untuk provider openai-compatible")
    api_key: str = Field("", description="API key user (dipakai sekali, tidak disimpan)")
    model: str = Field("", description="Model LLM pilihan user")


class FilmCaption(BaseModel):
    index: int = 0
    caption: str = ""
    hashtags: str = ""


class FilmCaptionsResponse(BaseModel):
    title: str = ""
    count: int = 0
    captions: List[FilmCaption] = Field(default_factory=list)
