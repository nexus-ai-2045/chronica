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


def build_raw_payload(message: "discord.Message") -> dict[str, Any]:
    """discord.Message から専用列 (message_id/content/author_id 等) に無い情報だけを抽出する。

    専用列と重複する情報 (id / content / author_id など) は入れない。
    値が取れない/空の項目はキーごと省略する (None を並べて水増ししない)。
    トークンや webhook 実行用シークレットは discord.Message / discord.Embed の
    公開属性経由では取得できないため、ここに混入する経路は無い。
    """
    payload: dict[str, Any] = {}

    embeds = getattr(message, "embeds", None)
    if embeds:
        payload["embeds"] = [e.to_dict() for e in embeds]

    reactions = getattr(message, "reactions", None)
    if reactions:
        payload["reactions"] = [
            {"emoji": str(r.emoji), "count": getattr(r, "count", None)} for r in reactions
        ]

    mentions = getattr(message, "mentions", None)
    if mentions:
        payload["mentions"] = [str(u.id) for u in mentions]

    raw_role_mentions = getattr(message, "raw_role_mentions", None)
    if raw_role_mentions:
        payload["mention_roles"] = [str(rid) for rid in raw_role_mentions]
    else:
        role_mentions = getattr(message, "role_mentions", None)
        if role_mentions:
            payload["mention_roles"] = [str(r.id) for r in role_mentions]

    if getattr(message, "mention_everyone", False):
        payload["mention_everyone"] = True

    if getattr(message, "pinned", False):
        payload["pinned"] = True

    if getattr(message, "tts", False):
        payload["tts"] = True

    msg_type = getattr(message, "type", None)
    if msg_type is not None:
        payload["type"] = getattr(msg_type, "name", str(msg_type))

    flags = getattr(message, "flags", None)
    if flags is not None:
        flags_value = getattr(flags, "value", None)
        if flags_value:
            payload["flags"] = flags_value

    webhook_id = getattr(message, "webhook_id", None)
    if webhook_id is not None:
        payload["webhook_id"] = str(webhook_id)

    stickers = getattr(message, "stickers", None)
    if stickers:
        payload["sticker_items"] = [
            {
                "id": str(getattr(s, "id", "")),
                "name": getattr(s, "name", None),
                "format": getattr(getattr(s, "format", None), "name", None),
            }
            for s in stickers
        ]

    edited_at = getattr(message, "edited_at", None)
    if edited_at is not None:
        payload["edited_at"] = edited_at.isoformat()

    attachments = getattr(message, "attachments", None)
    if attachments:
        payload["attachments"] = [
            {
                "id": str(a.id),
                "filename": a.filename,
                "url": a.url,
                "proxy_url": getattr(a, "proxy_url", None),
                "content_type": getattr(a, "content_type", None),
                "size": getattr(a, "size", None),
                "width": getattr(a, "width", None),
                "height": getattr(a, "height", None),
                "ephemeral": getattr(a, "ephemeral", None),
            }
            for a in attachments
        ]

    return payload


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
        raw=build_raw_payload(message),
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
        # payload.data は Discord から届く生ペイロードそのもの。専用列に無い情報の
        # 再解釈用として raw_json にそのまま保存する (指摘1: raw_json が名ばかり対応)。
        store.mark_edited(self.conn, str(payload.message_id), new_content, raw=data)

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
