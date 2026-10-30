from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DownloadSource(str, Enum):
    CIVITAI = "civitai"
    HUGGING_FACE = "hugging_face"
    DIRECT = "direct"


class ModelKind(str, Enum):
    CHECKPOINT = "checkpoint"
    DIFFUSION_MODEL = "diffusion_model"
    LORA = "lora"
    VAE = "vae"
    CONTROLNET = "controlnet"
    TEXT_ENCODER = "text_encoder"
    CLIP_VISION = "clip_vision"
    UPSCALE_MODEL = "upscale_model"
    EMBEDDING = "embedding"
    OTHER = "other"

    @property
    def relative_folder(self) -> Path | None:
        values = {
            ModelKind.CHECKPOINT: "checkpoints",
            ModelKind.DIFFUSION_MODEL: "diffusion_models",
            ModelKind.LORA: "loras",
            ModelKind.VAE: "vae",
            ModelKind.CONTROLNET: "controlnet",
            ModelKind.TEXT_ENCODER: "text_encoders",
            ModelKind.CLIP_VISION: "clip_vision",
            ModelKind.UPSCALE_MODEL: "upscale_models",
            ModelKind.EMBEDDING: "embeddings",
        }
        value = values.get(self)
        return Path("models") / value if value else None


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    source: DownloadSource
    name: str
    url: str
    size_bytes: int | None = None
    model_name: str | None = None
    version_name: str | None = None
    file_id: int | None = None


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    candidate: DownloadCandidate
    destination: Path
    civitai_token: str | None = None
    huggingface_token: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    succeeded: bool
    request: DownloadRequest
    path: Path | None = None
    bytes_written: int = 0
    detail: str = ""
    cancelled: bool = False


class QueueEntryState(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class DownloadQueueEntry:
    operation_id: str
    request: DownloadRequest
    state: QueueEntryState = QueueEntryState.QUEUED
