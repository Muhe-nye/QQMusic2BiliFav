from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from source.models import FolderVideo, MatchedVideo, Playlist, Song


class MusicProvider(ABC):
    @abstractmethod
    async def get_playlist(self, playlist_id: int) -> Playlist:
        raise NotImplementedError


class VideoPlatform(ABC):
    @abstractmethod
    async def ensure_folder(self, folder_name: str) -> tuple[int, str]:
        raise NotImplementedError

    @abstractmethod
    async def search_song(self, song: Song) -> MatchedVideo | None:
        raise NotImplementedError

    @abstractmethod
    async def add_video_to_folder(self, folder_id: int, video: MatchedVideo) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_folder_bvids(self, folder_id: int) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    async def list_folder_videos(self, folder_id: int) -> list[FolderVideo]:
        raise NotImplementedError
