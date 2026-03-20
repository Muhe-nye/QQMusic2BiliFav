from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

try:
    from dotenv import load_dotenv

    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

    def load_dotenv(*args, **kwargs):
        return False


@dataclass
class BiliConfig:
    sessdata: str
    bili_jct: str
    dede_user_id: str
    buvid3: str = ""

    @classmethod
    def from_env(cls, allow_empty: bool = False) -> "BiliConfig":
        sessdata = os.getenv("BILI_SESSDATA", "")
        bili_jct = os.getenv("BILI_JCT", "")
        dede_user_id = os.getenv("BILI_DEDEUSERID", os.getenv("DEDEUSERID", ""))
        buvid3 = os.getenv("BILI_BUVID3", "")
        if allow_empty:
            return cls(sessdata=sessdata, bili_jct=bili_jct, dede_user_id=dede_user_id, buvid3=buvid3)

        missing = []
        if not sessdata:
            missing.append("BILI_SESSDATA")
        if not bili_jct:
            missing.append("BILI_JCT")
        if not dede_user_id:
            missing.append("BILI_DEDEUSERID")
        if missing:
            raise ValueError(f"缺少环境变量: {', '.join(missing)}")
        return cls(sessdata=sessdata, bili_jct=bili_jct, dede_user_id=dede_user_id, buvid3=buvid3)


def load_local_env(env_file: str = "") -> list[Path]:
    candidates = [Path(env_file)] if env_file else [Path(".env.local"), Path(".env")]
    loaded: list[Path] = []

    for env_path in candidates:
        if not env_path.exists():
            continue
        if not HAS_DOTENV:
            logger.warning(f"检测到环境文件 {env_path}，但未安装 python-dotenv，已跳过自动加载")
            continue
        load_dotenv(dotenv_path=env_path, override=False)
        loaded.append(env_path)

    if loaded:
        logger.info(f"已加载环境文件: {', '.join(str(p) for p in loaded)}")
    return loaded


def load_song_mids_by_status(report_path: str, statuses: set[str]) -> set[str]:
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到报告文件: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results") or []
    mids: set[str] = set()
    for item in results:
        if item.get("status") not in statuses:
            continue
        song = item.get("song") or {}
        mid = str(song.get("mid") or "").strip()
        if mid:
            mids.add(mid)
    return mids
