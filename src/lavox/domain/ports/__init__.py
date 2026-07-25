"""Puertos (`Protocol`) que definen los límites del dominio LAVOX."""

from lavox.domain.ports.audio_provider_port import AudioProviderPort
from lavox.domain.ports.llm_port import JSONValue, LLMPort
from lavox.domain.ports.video_assembler_port import VideoAssemblerPort
from lavox.domain.ports.video_downloader_port import DownloadResult, VideoDownloaderPort
from lavox.domain.ports.video_search_port import VideoSearchPort

__all__ = [
    "AudioProviderPort",
    "DownloadResult",
    "JSONValue",
    "LLMPort",
    "VideoAssemblerPort",
    "VideoDownloaderPort",
    "VideoSearchPort",
]
