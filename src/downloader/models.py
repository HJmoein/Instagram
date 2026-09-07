from dataclasses import dataclass
from pathlib import Path
from enum import StrEnum


class MediaKind(StrEnum):
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
