from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from source.interfaces import MusicProvider, VideoPlatform
from source.models import FolderVideo
from source.models import Song


class SyncEngine:
    def __init__(self, provider: MusicProvider, platform: VideoPlatform, *, concurrency: int = 3):
        self.provider = provider
        self.platform = platform
        self.concurrency = concurrency

    async def run(
        self,
        qq_playlist_id: int,
        override_folder_name: str | None = None,
        max_songs: int = 0,
        only_song_mids: set[str] | None = None,
        sync_mode: str = "incremental",
    ) -> dict[str, Any]:
        playlist = await self.provider.get_playlist(qq_playlist_id)
        requested_folder_name = override_folder_name or playlist.bili_folder_name
        folder_id, actual_folder_name = await self.platform.ensure_folder(requested_folder_name)
        logger.info(f"开始同步 -> 收藏夹: {actual_folder_name} | 模式: {sync_mode}")

        semaphore = asyncio.Semaphore(self.concurrency)
        bvid_lock = asyncio.Lock()
        results: list[dict[str, Any]] = []
        songs = playlist.songs[:max_songs] if max_songs > 0 else playlist.songs
        if only_song_mids is not None:
            songs = [song for song in songs if song.mid in only_song_mids]

        folder_videos = await self.platform.list_folder_videos(folder_id)
        bili_bvids_before = {v.bvid for v in folder_videos}
        existing_bvids = set(bili_bvids_before)
        planned_bvids: set[str] = set()
        logger.info(f"扫描结果: QQ歌曲={playlist.song_num} | B站视频={len(bili_bvids_before)}")

        async def sync_one(song: Song):
            async with semaphore:
                try:
                    async with bvid_lock:
                        folder_hit = self._match_song_in_folder(song, folder_videos)
                        if folder_hit is not None:
                            result = {
                                "song": song.model_dump(),
                                "status": "skipped",
                                "reason": "收藏夹已命中同曲（收藏夹优先匹配）",
                                "video": {
                                    "bvid": folder_hit.bvid,
                                    "title": folder_hit.title,
                                    "duration": folder_hit.duration,
                                    "author": folder_hit.author,
                                },
                            }
                            results.append(result)
                            logger.info(f"收藏夹命中跳过: {song.title} -> {folder_hit.bvid}")
                            return

                    matched = await self.platform.search_song(song)
                    if matched is None:
                        result = {
                            "song": song.model_dump(),
                            "status": "skipped",
                            "reason": "未找到满足阈值的视频",
                        }
                        results.append(result)
                        logger.warning(f"跳过: {song.title} | {result['reason']}")
                        return

                    async with bvid_lock:
                        if matched.bvid in existing_bvids:
                            reason = "B站收藏夹已存在该视频（增量跳过）" if sync_mode == "incremental" else "B站收藏夹已存在该视频"
                            result = {
                                "song": song.model_dump(),
                                "status": "skipped",
                                "reason": reason,
                                "video": matched.model_dump(),
                            }
                            results.append(result)
                            logger.info(f"已存在跳过: {song.title} -> {matched.bvid}")
                            return
                        if matched.bvid in planned_bvids:
                            result = {
                                "song": song.model_dump(),
                                "status": "skipped",
                                "reason": "本次任务已匹配到相同视频（重复跳过）",
                                "video": matched.model_dump(),
                            }
                            results.append(result)
                            logger.info(f"重复跳过: {song.title} -> {matched.bvid}")
                            return
                        planned_bvids.add(matched.bvid)

                    await self.platform.add_video_to_folder(folder_id, matched)
                    async with bvid_lock:
                        planned_bvids.discard(matched.bvid)
                        existing_bvids.add(matched.bvid)
                        folder_videos.append(
                            FolderVideo(
                                bvid=matched.bvid,
                                title=matched.title,
                                duration=matched.duration,
                                author=matched.author,
                            )
                        )
                    status = "matched" if getattr(self.platform, "dry_run", False) else "added"
                    result = {
                        "song": song.model_dump(),
                        "status": status,
                        "video": matched.model_dump(),
                    }
                    results.append(result)
                    logger.success(f"{song.title} -> {matched.title} ({matched.bvid})")
                except Exception as exc:
                    async with bvid_lock:
                        if "matched" in locals() and matched is not None:
                            planned_bvids.discard(matched.bvid)
                    logger.error(f"失败: {song.title} | {exc}")
                    results.append(
                        {
                            "song": song.model_dump(),
                            "status": "error",
                            "reason": str(exc),
                        }
                    )

        await asyncio.gather(*(sync_one(song) for song in songs))

        added = sum(1 for r in results if r["status"] == "added")
        matched = sum(1 for r in results if r["status"] == "matched")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        errors = sum(1 for r in results if r["status"] == "error")
        duplicate_video_mappings = self._collect_duplicate_video_mappings(results)
        unique_video_count = len(existing_bvids)

        if duplicate_video_mappings:
            logger.warning(
                f"检测到重复映射: {len(duplicate_video_mappings)} 组，歌曲数可能大于视频数（当前收藏夹视频数: {unique_video_count}）"
            )
            for group in duplicate_video_mappings:
                song_labels = ", ".join(f"{s['title']}[{s['mid']}]" for s in group["songs"])
                logger.warning(f"重复视频 {group['bvid']} <- {song_labels}")
        else:
            logger.info(f"本次无重复映射，收藏夹视频数: {unique_video_count}")

        return {
            "playlist": {
                "id": playlist.id,
                "name": playlist.name,
                "song_num": playlist.song_num,
                "processed_song_num": len(songs),
                "target_folder_name": actual_folder_name,
                "requested_folder_name": requested_folder_name,
                "target_folder_id": folder_id,
                "sync_mode": sync_mode,
                "bili_video_num_before": len(bili_bvids_before),
                "bili_video_num_after": len(existing_bvids),
            },
            "summary": {
                "added": added,
                "matched": matched,
                "skipped": skipped,
                "errors": errors,
                "duplicate_mapping_groups": len(duplicate_video_mappings),
                "correct_video_count": unique_video_count,
            },
            "duplicates": duplicate_video_mappings,
            "results": results,
        }

    @staticmethod
    def _match_song_in_folder(song: Song, folder_videos: list[FolderVideo]) -> FolderVideo | None:
        title_aliases = SyncEngine._title_aliases(song.title)
        artists = [SyncEngine._normalize(a) for a in song.artists if a]
        if not title_aliases:
            return None

        for video in folder_videos:
            title = SyncEngine._normalize(video.title)
            if not any(alias and alias in title for alias in title_aliases):
                continue
            duration_ok = video.duration <= 0 or abs(video.duration - song.duration) <= 25
            artist_hit = any(artist and artist in title for artist in artists)
            if artist_hit or duration_ok:
                return video
        return None

    @staticmethod
    def _normalize(text: str) -> str:
        lowered = (text or "").lower()
        return re.sub(r"[\[\]【】()（）{}<>《》'\"\-_/\\|,.;:!?\s]+", "", lowered)

    @staticmethod
    def _title_aliases(title: str) -> set[str]:
        aliases: set[str] = set()
        if not title:
            return aliases
        aliases.add(SyncEngine._normalize(title))

        # 括号中常见中英文别名：One Last Kiss (最后一吻)
        for part in re.findall(r"[（(]([^()（）]+)[）)]", title):
            normalized = SyncEngine._normalize(part)
            if normalized:
                aliases.add(normalized)

        main = re.sub(r"[（(][^()（）]+[）)]", "", title)
        normalized_main = SyncEngine._normalize(main)
        if normalized_main:
            aliases.add(normalized_main)

        aliases.discard("")
        return aliases

    @staticmethod
    def _collect_duplicate_video_mappings(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in results:
            video = item.get("video") or {}
            bvid = str(video.get("bvid") or "").strip()
            if not bvid:
                continue
            song = item.get("song") or {}
            grouped.setdefault(bvid, []).append(
                {
                    "mid": str(song.get("mid") or ""),
                    "title": str(song.get("title") or ""),
                }
            )

        duplicates: list[dict[str, Any]] = []
        for bvid, songs in grouped.items():
            unique_song_keys = {(s["mid"], s["title"]) for s in songs if s["mid"] or s["title"]}
            if len(unique_song_keys) <= 1:
                continue
            deduped_songs = [{"mid": mid, "title": title} for mid, title in sorted(unique_song_keys)]
            duplicates.append(
                {
                    "bvid": bvid,
                    "song_count": len(deduped_songs),
                    "songs": deduped_songs,
                }
            )
        duplicates.sort(key=lambda x: (-x["song_count"], x["bvid"]))
        return duplicates
