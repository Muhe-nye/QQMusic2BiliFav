from __future__ import annotations

from loguru import logger
from qqmusic_api import songlist

from source.interfaces import MusicProvider
from source.models import Playlist


class QQMusicClient(MusicProvider):
    async def get_playlist(self, playlist_id: int) -> Playlist:
        detail = await songlist.get_detail(songlist_id=playlist_id, num=100, onlysong=False)
        detail["songlist"] = await songlist.get_songlist(songlist_id=playlist_id)
        playlist = Playlist.from_qq_payload(detail)
        logger.info(f"QQ 歌单读取成功: {playlist.name} ({playlist.song_num} 首)")
        return playlist
