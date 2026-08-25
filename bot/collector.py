#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chronica Discord Bot 本体。

対象サーバーのメッセージをリアルタイムに SQLite (store.py) へ蓄積する。
allowlist 外の guild のイベントはすべて無視する。

実行には discord.py と実際の Bot token が必要 (このファイル単体は import 時点では
discord.py が無いと動かないが、store.py / export_v2.py はこのモジュールに依存しない
ので discord.py 未インストールでもテストできる)。

起動:
    python collector.py
"""

from __future__ import annotations

import logging
from typing import Any

import discord

import envutil
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chronica.collector")


def build_intents() -> "discord.Intents":
    """収集に必要な最小限の intents を組み立てる。"""
    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True
    return intents


def resolve_thread_parent_id(channel: Any) -> str | None:
    """スレッド (フォーラム投稿含む) の場合は親チャンネル ID を返す。通常チャンネルは None。"""
    parent = getattr(channel, "parent", None)
    if parent is not None:
        return str(parent.id)
    return None


def message_to_record(message: "discord.Message", source: str = "gateway") -> store.MessageRecord:
    """discord.Message から MessageRecord を組み立てる。"""
    reply_to_id = None
    ref = getattr(message, "reference", None)
    if ref is not None and getattr(ref, "message_id", None):
        reply_to_id = str(ref.message_id)

    attachments = [
        {
            "id": str(a.id),
            "filename": a.filename,
            "url": a.url,
            "content_type": getattr(a, "content_type", None),
            "size": getattr(a, "size", None),
        }
        for a in message.attachments
    ]

    return store.MessageRecord(
        message_id=str(message.id),
        guild_id=str(message.guild.id) if message.guild else "",
        channel_id=str(message.channel.id),
        thread_parent_id=resolve_thread_parent_id(message.channel),
        author_id=str(message.author.id),
        author_name=str(message.author.display_name or message.author.name),
        ts=message.created_at.isoformat(),
        edited_at=message.edited_at.isoformat() if message.edited_at else None,
        content=message.content or "",
        attachments=attachments,
        reply_to_id=reply_to_id,
        raw={"id": str(message.id), "content": message.content, "author_id": str(message.author.id)},
        source=source,
    )


class ChronicaCollector(discord.Client):
    """対象サーバーのメッセージを SQLite に蓄積する Bot クライアント。"""

    def __init__(self, db_path: str, allowlist: set[str], **kwargs: Any) -> None:
        super().__init__(intents=build_intents(), **kwargs)
        self.db_path = db_path
        self.allowlist = allowlist
        self.conn = store.get_connection(db_path)
        store.init_db(self.conn)

    def is_allowed_guild(self, guild_id: Any) -> bool:
        """guild_id が allowlist に含まれるかを判定する。allowlist が空なら常に False。"""
        if guild_id is None:
            return False
        return str(guild_id) in self.allowlist

    async def on_ready(self) -> None:
        logger.info("ログイン完了: %s", self.user)
        for guild in self.guilds:
            if not self.is_allowed_guild(guild.id):
                continue
            await self._sync_guild_channels(guild)

    async def _sync_guild_channels(self, guild: "discord.Guild") -> None:
        """allowlist guild のチャンネル一覧 (スレッド含む) を upsert する。"""
        for channel in guild.channels:
            store.upsert_channel(
                self.conn,
                channel_id=str(channel.id),
                guild_id=str(guild.id),
                name=getattr(channel, "name", str(channel.id)),
                type_=str(channel.type),
                parent_id=None,
            )
        for thread in guild.threads:
            store.upsert_channel(
                self.conn,
                channel_id=str(thread.id),
                guild_id=str(guild.id),
                name=getattr(thread, "name", str(thread.id)),
                type_=str(thread.type),
                parent_id=str(thread.parent_id) if thread.parent_id else None,
            )
        logger.info("guild %s: チャンネル同期完了", guild.id)

    async def on_message(self, message: "discord.Message") -> None:
        if message.guild is None or not self.is_allowed_guild(message.guild.id):
            return
        if message.author.bot and message.author.id == (self.user.id if self.user else None):
            return
        record = message_to_record(message, source="gateway")
        store.upsert_message(self.conn, record)

    async def on_raw_message_edit(self, payload: "discord.RawMessageUpdateEvent") -> None:
        guild_id = payload.guild_id
        if guild_id is None or not self.is_allowed_guild(guild_id):
            return
        data = payload.data or {}
        new_content = data.get("content")
        if new_content is None:
            # content が含まれない編集イベント (embed 更新など) は無視する
            return
        store.mark_edited(self.conn, str(payload.message_id), new_content)

    async def on_raw_message_delete(self, payload: "discord.RawMessageDeleteEvent") -> None:
        guild_id = payload.guild_id
        if guild_id is None or not self.is_allowed_guild(guild_id):
            return
        store.mark_deleted(self.conn, str(payload.message_id))

    async def on_raw_bulk_message_delete(self, payload: "discord.RawBulkMessageDeleteEvent") -> None:
        guild_id = payload.guild_id
        if guild_id is None or not self.is_allowed_guild(guild_id):
            return
        for message_id in payload.message_ids:
            store.mark_deleted(self.conn, str(message_id))


def main() -> None:
    envutil.load_dotenv()
    token = envutil.get_bot_token()
    allowlist = envutil.get_guild_allowlist()
    db_path = envutil.get_db_path()

    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN が未設定です (.env を確認してください)")
    if not allowlist:
        logger.warning("GUILD_ALLOWLIST が空です。全 guild のイベントを無視します")

    client = ChronicaCollector(db_path=db_path, allowlist=allowlist)
    client.run(token)


if __name__ == "__main__":
    main()
