#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
.env の手動パーサ。python-dotenv に依存しないための最小実装。

collector.py / backfill.py / export_v2.py から共通で使う。
DISCORD_BOT_TOKEN / GUILD_ALLOWLIST / CHRONICA_DB / EXPORT_OUT を .env または
既存の os.environ から読む。
"""

from __future__ import annotations

import os
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = BOT_DIR / ".env"


def load_dotenv(path: str | Path | None = None) -> None:
    """.env を読み os.environ に設定する (既に環境変数に値があれば上書きしない)。

    ファイルが無ければ何もしない (エラーにしない)。
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 前後のクォートを外す
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def get_guild_allowlist() -> set[str]:
    """GUILD_ALLOWLIST (カンマ区切り) を集合として返す。未設定なら空集合 (全拒否)。"""
    raw = os.environ.get("GUILD_ALLOWLIST", "")
    return {g.strip() for g in raw.split(",") if g.strip()}


def get_bot_token() -> str | None:
    """DISCORD_BOT_TOKEN を返す。未設定なら None。"""
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    return token or None


def get_db_path() -> str:
    """CHRONICA_DB のパスを返す。未設定なら既定値 ../data/chronica.db (bot/ 基準)。"""
    return os.environ.get("CHRONICA_DB", str(BOT_DIR.parent / "data" / "chronica.db"))


def get_export_out_path() -> str:
    """EXPORT_OUT のパスを返す。未設定なら既定値 ../data/chronica-v2-data.js (bot/ 基準)。"""
    return os.environ.get("EXPORT_OUT", str(BOT_DIR.parent / "data" / "chronica-v2-data.js"))
