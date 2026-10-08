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
