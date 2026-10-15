from dataclasses import dataclass
from pathlib import Path
from enum import Enum


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    path: Path
    kind: MediaKind
    title: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    files: list[DownloadedFile]
    title: str
    extractor: str
    caption: str = ""


@dataclass(frozen=True, slots=True)
class CachedMedia:
    file_id: str
    kind: MediaKind


@dataclass(frozen=True, slots=True)
class CachedDownload:
    files: list[CachedMedia]
    title: str
    caption: str