#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chronica 履歴バックフィル CLI。

allowlist guild の全テキストチャンネル + アクティブ/アーカイブ済みスレッドを
channel.history(limit=None, oldest_first=True) で走査し、store.upsert_message で
SQLite に蓄積する (source='backfill')。

sync_state で再開可能。--incremental を付けると newest_id 以降の差分のみ取得する。

実行には discord.py と実際の Bot token が必要。store.py はこのモジュールに依存しない
ので discord.py 未インストールでもテストできる。

使い方:
    python backfill.py                  # 全チャンネル・全期間バックフィル (再開可能)
    python backfill.py --incremental    # 各チャンネルの newest_id 以降だけ取得
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from typing import Any

import discord

import envutil
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chronica.backfill")

# チャンネル間の小 sleep (秒)。レート制限は discord.py 任せだが、連続チャンネル走査時の
# バースト緩和として最小限入れる。
CHANNEL_SLEEP_SEC = 1.0


def build_intents() -> "discord.Intents":
    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True
    return intents


def message_to_record(message: "discord.Message") -> store.MessageRecord:
    """collector.message_to_record と同等の変換を backfill 用に行う (source='backfill')。"""
    from collector import message_to_record as _convert  # 循環 import 回避のため遅延 import

    return _convert(message, source="backfill")


async def iter_target_channels(guild: "discord.Guild") -> list[Any]:
    """バックフィル対象のチャンネル (テキストチャンネル + アクティブ/アーカイブ済みスレッド) を集める。"""
    targets: list[Any] = []
    for channel in guild.text_channels:
        targets.append(channel)
        for thread in channel.threads:
            targets.append(thread)
        try:
            async for archived_thread in channel.archived_threads(limit=None):
                targets.append(archived_thread)
        except discord.Forbidden:
            logger.warning("archived_threads 取得権限なし: channel=%s", channel.id)
        except discord.HTTPException as exc:
            logger.warning("archived_threads 取得失敗: channel=%s err=%s", channel.id, exc)

    # フォーラムチャンネル (投稿=スレッド) にも対応
    for forum in getattr(guild, "forums", []):
        for thread in forum.threads:
            targets.append(thread)
        try:
            async for archived_thread in forum.archived_threads(limit=None):
                targets.append(archived_thread)
        except discord.Forbidden:
            logger.warning("archived_threads (forum) 取得権限なし: channel=%s", forum.id)
        except discord.HTTPException as exc:
            logger.warning("archived_threads (forum) 取得失敗: channel=%s err=%s", forum.id, exc)

    return targets


async def backfill_channel(conn: Any, channel: Any, incremental: bool) -> None:
    """1 チャンネル分のバックフィルを実行する (再開可能)。"""
    channel_id = str(channel.id)
    state = store.get_sync_state(conn, channel_id)

    if incremental:
        after_id = int(state["newest_id"]) if state and state.get("newest_id") else None
        after = discord.Object(id=after_id) if after_id else None
        oldest_first = True
    elif state and state.get("backfill_done"):
        logger.info("channel %s: バックフィル済みのためスキップ (--incremental で差分取得可)", channel_id)
        return
    else:
        # 再開: oldest_id があればそこより前だけ取得し直す必要はない (oldest_first で
        # 最初から辿るが、既存 message_id は upsert で冪等なので安全)
        after = None
        oldest_first = True

    oldest_seen: str | None = state["oldest_id"] if state else None
    newest_seen: str | None = state["newest_id"] if state else None
    count = 0

    try:
        async for message in channel.history(limit=None, oldest_first=oldest_first, after=after):
            record = message_to_record(message)
            store.upsert_message(conn, record)
            count += 1
            mid = str(message.id)
            if oldest_seen is None or int(mid) < int(oldest_seen):
                oldest_seen = mid
            if newest_seen is None or int(mid) > int(newest_seen):
                newest_seen = mid
    except discord.Forbidden:
        logger.warning("channel %s: 閲覧権限なし。スキップ", channel_id)
        return
    except discord.HTTPException as exc:
        logger.error("channel %s: history 取得失敗 err=%s (途中まで保存済み)", channel_id, exc)
        store.upsert_sync_state(conn, channel_id, backfill_done=False, oldest_id=oldest_seen, newest_id=newest_seen)
        return

    store.upsert_sync_state(conn, channel_id, backfill_done=True, oldest_id=oldest_seen, newest_id=newest_seen)
    logger.info("channel %s: %d 件取得完了", channel_id, count)


async def run_backfill(token: str, allowlist: set[str], db_path: str, incremental: bool) -> None:
    intents = build_intents()
    client = discord.Client(intents=intents)
    conn = store.get_connection(db_path)
    store.init_db(conn)

    @client.event
    async def on_ready() -> None:
        try:
            for guild in client.guilds:
                if str(guild.id) not in allowlist:
                    continue
                logger.info("guild %s: バックフィル開始", guild.id)
                targets = await iter_target_channels(guild)
                for channel in targets:
                    await backfill_channel(conn, channel, incremental)
                    time.sleep(CHANNEL_SLEEP_SEC)
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronica 履歴バックフィル")
    parser.add_argument("--incremental", action="store_true", help="newest_id 以降の差分のみ取得する")
    args = parser.parse_args()

    envutil.load_dotenv()
    token = envutil.get_bot_token()
    allowlist = envutil.get_guild_allowlist()
    db_path = envutil.get_db_path()

    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN が未設定です (.env を確認してください)")
    if not allowlist:
        raise SystemExit("GUILD_ALLOWLIST が未設定です (.env を確認してください)")

    asyncio.run(run_backfill(token, allowlist, db_path, args.incremental))


if __name__ == "__main__":
    main()
