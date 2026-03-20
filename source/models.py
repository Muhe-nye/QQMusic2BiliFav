from __future__ import annotations

import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


class Song(BaseModel):
    mid: str
    title: str
    artists: List[str]
    album: Optional[str] = None
    duration: int
    mv_vid: Optional[str] = None

    @property
    def search_keyword(self) -> str:
        artist_str = " ".join(self.artists)
        return f"{artist_str} {self.title}".strip()

    @field_validator("artists", mode="before")
    @classmethod
    def ensure_list(cls, value: Any):
        if isinstance(value, str):
            return [value]
        return value

    @classmethod
    def from_qq_payload(cls, payload: dict[str, Any]) -> "Song":
        singers = payload.get("singer") or []
        artists = [s.get("name", "") for s in singers if s.get("name")]
        album = (payload.get("album") or {}).get("name")
        mv_vid = (payload.get("mv") or {}).get("vid")
        return cls(
            mid=payload.get("mid", ""),
            title=payload.get("title") or payload.get("name", ""),
            artists=artists or ["未知歌手"],
            album=album,
            duration=int(payload.get("interval", 0)),
            mv_vid=mv_vid,
        )


class Playlist(BaseModel):
    id: int
    name: str
    songs: List[Song]

    @property
    def song_num(self) -> int:
        return len(self.songs)

    @property
    def bili_folder_name(self) -> str:
        return self.name

    @classmethod
    def from_qq_payload(cls, payload: dict[str, Any]) -> "Playlist":
        dirinfo = payload.get("dirinfo") or {}
        raw_songs = payload.get("songlist") or []
        songs = [Song.from_qq_payload(song) for song in raw_songs]
        playlist_id = dirinfo.get("id") or payload.get("disstid") or 0
        name = dirinfo.get("title") or f"QQ歌单-{playlist_id}"
        return cls(id=int(playlist_id), name=name, songs=songs)


class MatchedVideo(BaseModel):
    aid: int
    bvid: str
    title: str
    duration: int
    author: str
    play: int = 0
    score: float
    query: str
    reasons: List[str] = Field(default_factory=list)


class FolderVideo(BaseModel):
    bvid: str
    title: str
    duration: int = 0
    author: str = ""


def clean_html_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()
