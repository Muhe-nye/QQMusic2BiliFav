from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any, Optional

import httpx
from loguru import logger

from source.config import BiliConfig
from source.interfaces import VideoPlatform
from source.models import FolderVideo, MatchedVideo, Song, clean_html_text


class BilibiliClient(VideoPlatform):
    SEARCH_ENDPOINT = "https://api.bilibili.com/x/web-interface/search/type"
    FOLDER_LIST_ENDPOINT = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
    FOLDER_ADD_ENDPOINT = "https://api.bilibili.com/x/v3/fav/folder/add"
    FAV_DEAL_ENDPOINT = "https://api.bilibili.com/x/v3/fav/resource/deal"
    FAV_RESOURCE_LIST_ENDPOINT = "https://api.bilibili.com/x/v3/fav/resource/list"

    def __init__(
        self,
        config: BiliConfig,
        *,
        dry_run: bool = False,
        score_threshold: float = 52.0,
        duration_tolerance: int = 25,
        request_interval: float = 0.35,
        retry_times: int = 3,
        retry_base_delay: float = 1.0,
    ):
        self.config = config
        self.dry_run = dry_run
        self.score_threshold = score_threshold
        self.duration_tolerance = duration_tolerance
        self.request_interval = max(0.0, request_interval)
        self.retry_times = max(0, retry_times)
        self.retry_base_delay = max(0.1, retry_base_delay)
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            cookies={
                "SESSDATA": config.sessdata,
                "bili_jct": config.bili_jct,
                "DedeUserID": config.dede_user_id,
                "buvid3": config.buvid3,
            },
        )

    async def close(self):
        await self.client.aclose()

    async def ensure_folder(self, folder_name: str) -> tuple[int, str]:
        if self.dry_run and not self.config.dede_user_id:
            logger.warning("dry-run 且未提供 DedeUserID，跳过收藏夹检查")
            return -1, folder_name

        existing_id = await self._find_folder(folder_name)
        if existing_id:
            logger.info(f"使用已存在收藏夹: {folder_name} ({existing_id})")
            return existing_id, folder_name

        if self.dry_run:
            logger.warning(f"dry-run 模式：跳过创建收藏夹 {folder_name}")
            return -1, folder_name

        try:
            created_id = await self._create_folder(folder_name)
            return created_id, folder_name
        except Exception:
            # 可能是并发创建或重名冲突，先再查一次原名
            existing_id = await self._find_folder(folder_name)
            if existing_id:
                logger.info(f"检测到重名，复用已存在收藏夹: {folder_name} ({existing_id})")
                return existing_id, folder_name

        suffixed_name = folder_name if folder_name.endswith("（歌单）") else f"{folder_name}（歌单）"
        suffixed_id = await self._find_folder(suffixed_name)
        if suffixed_id:
            logger.info(f"检测到重名，使用已存在收藏夹: {suffixed_name} ({suffixed_id})")
            return suffixed_id, suffixed_name

        created_id = await self._create_folder(suffixed_name)
        return created_id, suffixed_name

    async def search_song(self, song: Song) -> MatchedVideo | None:
        return await self._match_video(song)

    async def add_video_to_folder(self, folder_id: int, video: MatchedVideo) -> None:
        if self.dry_run:
            return
        payload = {
            "rid": video.aid,
            "type": 2,
            "add_media_ids": str(folder_id),
            "del_media_ids": "",
            "csrf": self.config.bili_jct,
        }
        await self._post_json(self.FAV_DEAL_ENDPOINT, data=payload)

    async def list_folder_bvids(self, folder_id: int) -> set[str]:
        videos = await self.list_folder_videos(folder_id)
        return {video.bvid for video in videos if video.bvid}

    async def list_folder_videos(self, folder_id: int) -> list[FolderVideo]:
        if folder_id <= 0:
            return []

        videos: list[FolderVideo] = []
        page = 1
        page_size = 40
        while True:
            params = {
                "media_id": folder_id,
                "pn": page,
                "ps": page_size,
                "order": "mtime",
                "type": 0,
                "tid": 0,
                "platform": "web",
            }
            data = await self._get_json(self.FAV_RESOURCE_LIST_ENDPOINT, params=params)
            medias = (data.get("data") or {}).get("medias") or []
            if not medias:
                break
            for media in medias:
                bvid = str((media or {}).get("bvid") or "").strip()
                if not bvid:
                    continue
                videos.append(
                    FolderVideo(
                        bvid=bvid,
                        title=str((media or {}).get("title") or ""),
                        duration=int((media or {}).get("duration") or 0),
                        author=str(((media or {}).get("upper") or {}).get("name") or ""),
                    )
                )
            if len(medias) < page_size:
                break
            page += 1
        return videos

    async def _create_folder(self, folder_name: str) -> int:
        payload = {
            "title": folder_name,
            "intro": "来自 QQMusic2BiliFav 自动同步",
            "privacy": 0,
            "cover": "",
            "csrf": self.config.bili_jct,
        }
        data = await self._post_json(self.FOLDER_ADD_ENDPOINT, data=payload)
        folder_id = int((data.get("data") or {}).get("id") or 0)
        if not folder_id:
            raise RuntimeError(f"创建收藏夹失败: {data}")
        logger.info(f"创建收藏夹成功: {folder_name} ({folder_id})")
        return folder_id

    async def _find_folder(self, folder_name: str) -> int:
        if not self.config.dede_user_id:
            return 0
        params = {"up_mid": self.config.dede_user_id}
        data = await self._get_json(self.FOLDER_LIST_ENDPOINT, params=params)
        folders = (data.get("data") or {}).get("list") or []
        for folder in folders:
            if folder.get("title") == folder_name:
                return int(folder.get("id"))
        return 0

    async def _match_video(self, song: Song) -> Optional[MatchedVideo]:
        queries = [
            f"{song.search_keyword} MV",
            f"{song.search_keyword} 官方",
            song.search_keyword,
        ]

        best: Optional[MatchedVideo] = None
        for query in queries:
            candidates = await self._search_videos(query)
            for item in candidates:
                candidate = self._score_candidate(song, item, query)
                if candidate is None:
                    continue
                if best is None or candidate.score > best.score:
                    best = candidate
            if best and best.score >= self.score_threshold:
                break

        if best and best.score >= self.score_threshold:
            return best
        return None

    async def _search_videos(self, query: str) -> list[dict[str, Any]]:
        params = {
            "search_type": "video",
            "keyword": query,
            "order": "totalrank",
            "page": 1,
            "duration": 0,
        }
        data = await self._get_json(self.SEARCH_ENDPOINT, params=params)
        return (data.get("data") or {}).get("result") or []

    def _score_candidate(self, song: Song, item: dict[str, Any], query: str) -> Optional[MatchedVideo]:
        aid = item.get("aid")
        bvid = item.get("bvid")
        if not aid or not bvid:
            return None

        raw_title = clean_html_text(item.get("title", ""))
        normalized_title = self._normalize(raw_title)
        song_title = self._normalize(song.title)
        artists = [self._normalize(a) for a in song.artists]

        duration = self._parse_duration(item.get("duration", "0:00"))
        if duration <= 0:
            duration = int(item.get("arcurl_duration") or 0)
        if duration <= 0:
            return None

        diff = abs(duration - song.duration)
        if diff > max(self.duration_tolerance + 90, int(song.duration * 0.7)):
            return None

        score = 100.0
        reasons: list[str] = []

        if diff <= 3:
            score += 16
            reasons.append("时长几乎一致")
        elif diff <= 8:
            score += 9
            reasons.append("时长接近")
        elif diff <= self.duration_tolerance:
            score += 3
            reasons.append("时长可接受")
        else:
            penalty = min(40, (diff - self.duration_tolerance) * 0.8)
            score -= penalty
            reasons.append(f"时长偏差 {diff}s")

        if song_title and song_title in normalized_title:
            score += 14
            reasons.append("命中歌名")

        artist_hit = any(artist and artist in normalized_title for artist in artists)
        if artist_hit:
            score += 12
            reasons.append("命中歌手")

        for kw in ("mv", "官方", "official", "music video", "vevo"):
            if self._normalize(kw) in normalized_title:
                score += 9
                reasons.append(f"标题包含 {kw}")
                break

        for kw in ("完整版", "高音质", "lyrics", "歌词"):
            if self._normalize(kw) in normalized_title:
                score += 3
                reasons.append(f"标题包含 {kw}")
                break

        for kw in ("live", "演唱会", "cover", "翻唱", "伴奏", "dj"):
            if self._normalize(kw) in normalized_title:
                score -= 12
                reasons.append(f"降权词 {kw}")
                break

        play_count = self._parse_play(item.get("play", "0"))
        if play_count > 2_000_000:
            score += 7
        elif play_count > 200_000:
            score += 4
        elif play_count > 20_000:
            score += 2

        author = item.get("author", "")
        if self._normalize(author).find("官方") >= 0:
            score += 4
            reasons.append("UP 主疑似官方")

        return MatchedVideo(
            aid=int(aid),
            bvid=str(bvid),
            title=raw_title,
            duration=duration,
            author=author,
            play=play_count,
            score=round(score, 2),
            query=query,
            reasons=reasons,
        )

    async def _get_json(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        max_attempts = self.retry_times + 1
        for attempt in range(max_attempts):
            await self._throttle()
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                code = data.get("code", -1)
                if code == 0:
                    return data
                if self._is_retryable_code(code) and attempt < self.retry_times:
                    await self._sleep_before_retry(attempt, "GET", url, code=code)
                    continue
                raise RuntimeError(f"B 站请求失败: {url} | {data}")
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if self._is_retryable_status(status) and attempt < self.retry_times:
                    await self._sleep_before_retry(attempt, "GET", url, status=status)
                    continue
                raise

    async def _post_json(self, url: str, *, data: dict[str, Any]) -> dict[str, Any]:
        max_attempts = self.retry_times + 1
        for attempt in range(max_attempts):
            await self._throttle()
            try:
                response = await self.client.post(url, data=data)
                response.raise_for_status()
                payload = response.json()
                code = payload.get("code", -1)
                if code == 0:
                    return payload
                if self._is_retryable_code(code) and attempt < self.retry_times:
                    await self._sleep_before_retry(attempt, "POST", url, code=code)
                    continue
                raise RuntimeError(f"B 站请求失败: {url} | {payload}")
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if self._is_retryable_status(status) and attempt < self.retry_times:
                    await self._sleep_before_retry(attempt, "POST", url, status=status)
                    continue
                raise

    async def _throttle(self) -> None:
        if self.request_interval <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            wait_seconds = self.request_interval - (now - self._last_request_at)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._last_request_at = time.monotonic()

    async def _sleep_before_retry(
        self,
        attempt: int,
        method: str,
        url: str,
        *,
        status: int | None = None,
        code: int | None = None,
    ) -> None:
        delay = self.retry_base_delay * (2**attempt) + random.uniform(0, self.retry_base_delay * 0.4)
        reason = f"status={status}" if status is not None else f"code={code}"
        logger.warning(f"{method} 重试 {attempt + 1}/{self.retry_times} | {reason} | {url} | {delay:.2f}s 后重试")
        await asyncio.sleep(delay)

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in {412, 429, 500, 502, 503, 504}

    @staticmethod
    def _is_retryable_code(code: int) -> bool:
        return code in {-352, -412, -509, -799}

    @staticmethod
    def _parse_duration(text: str) -> int:
        if isinstance(text, int):
            return text
        parts = str(text).split(":")
        if not parts:
            return 0
        try:
            numbers = [int(p) for p in parts]
        except ValueError:
            return 0
        if len(numbers) == 2:
            return numbers[0] * 60 + numbers[1]
        if len(numbers) == 3:
            return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        return 0

    @staticmethod
    def _parse_play(value: Any) -> int:
        text = str(value).strip().lower().replace(",", "")
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)([万亿]?)$", text)
        if not match:
            try:
                return int(float(text))
            except ValueError:
                return 0
        number = float(match.group(1))
        unit = match.group(2)
        multiplier = 1
        if unit == "万":
            multiplier = 10_000
        elif unit == "亿":
            multiplier = 100_000_000
        return int(number * multiplier)

    @staticmethod
    def _normalize(text: str) -> str:
        lowered = text.lower()
        lowered = re.sub(r"[\[\]【】()（）{}<>《》'\"\-_/\\|,.;:!?\s]+", "", lowered)
        return lowered
