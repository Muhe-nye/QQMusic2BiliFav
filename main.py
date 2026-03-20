from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from loguru import logger

from source.config import BiliConfig, load_local_env, load_song_mids_by_status
from source.engine import SyncEngine
from source.platforms.bilibili import BilibiliClient
from source.providers.qq_music import QQMusicClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQ 音乐歌单同步到 Bilibili 收藏夹")
    parser.add_argument("--env-file", type=str, default="", help="指定环境变量文件（默认读取 .env.local 再 .env）")
    parser.add_argument("--qq-playlist-id", type=int, default=0, help="QQ 音乐歌单 ID")
    parser.add_argument("--folder-name", type=str, default="", help="目标 B 站收藏夹名称（默认使用 QQ 歌单名）")
    parser.add_argument(
        "--sync-mode",
        type=str,
        choices=["incremental", "copy"],
        default="incremental",
        help="同步模式：incremental 增量同步 / copy 全量复制",
    )
    parser.add_argument("--concurrency", type=int, default=3, help="并发数")
    parser.add_argument("--request-interval", type=float, default=1, help="请求最小间隔秒数（越大越慢）")
    parser.add_argument("--retry-times", type=int, default=3, help="遇到风控/服务错误时的重试次数")
    parser.add_argument("--retry-base-delay", type=float, default=2.0, help="重试基础退避秒数")
    parser.add_argument("--duration-tolerance", type=int, default=25, help="时长优先匹配容忍秒数")
    parser.add_argument("--score-threshold", type=float, default=52.0, help="候选视频最低得分")
    parser.add_argument("--max-songs", type=int, default=0, help="最多处理歌曲数，0 表示全量")
    parser.add_argument("--retry-from", type=str, default="", help="补跑来源报告路径")
    parser.add_argument(
        "--retry-mode",
        type=str,
        choices=["error", "skipped", "both"],
        default="both",
        help="补跑类型：error / skipped / both",
    )
    parser.add_argument("--dry-run", action="store_true", help="只匹配不写入 B 站")
    parser.add_argument("--report", type=str, default="sync_report.json", help="输出报告文件")
    return parser.parse_args()


async def main_async(args: argparse.Namespace):
    load_local_env(args.env_file)
    qq_playlist_id = args.qq_playlist_id or int(os.getenv("QQ_PLAYLIST_ID", "0") or "0")
    if not qq_playlist_id:
        raise ValueError("必须提供 --qq-playlist-id，或在环境变量设置 QQ_PLAYLIST_ID")

    config = BiliConfig.from_env(allow_empty=args.dry_run)
    retry_song_mids: set[str] | None = None
    if args.retry_from:
        retry_song_mids = set()
        statuses = {"error", "skipped"} if args.retry_mode == "both" else {args.retry_mode}
        selected_mids = load_song_mids_by_status(args.retry_from, statuses)
        logger.info(f"补跑模式 {args.retry_mode}: {len(selected_mids)} 首")
        retry_song_mids.update(selected_mids)
        logger.info(f"补跑合并后: {len(retry_song_mids)} 首")
        if not retry_song_mids:
            logger.warning("指定报告中没有可补跑歌曲，本次不会处理任何歌曲")

    platform = BilibiliClient(
        config,
        dry_run=args.dry_run,
        duration_tolerance=args.duration_tolerance,
        score_threshold=args.score_threshold,
        request_interval=args.request_interval,
        retry_times=args.retry_times,
        retry_base_delay=args.retry_base_delay,
    )
    engine = SyncEngine(QQMusicClient(), platform, concurrency=max(1, args.concurrency))

    try:
        report = await engine.run(
            qq_playlist_id,
            args.folder_name or None,
            max_songs=max(0, args.max_songs),
            only_song_mids=retry_song_mids,
            sync_mode=args.sync_mode,
        )
    finally:
        await platform.close()

    output_path = Path(args.report)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"同步完成，报告写入: {output_path.resolve()}")
    logger.info(f"统计: {report['summary']}")


def main():
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
