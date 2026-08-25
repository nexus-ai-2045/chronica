#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chronica bot の DB 層。

discord.py に依存しない (テストで実 Discord なしに検証できるようにするため)。
SQLite を WAL モードで開き、messages / channels / sync_state / messages_fts を操作する。

dedupe は message_id (PK) のみ。本文 hash は使わない (壁打ち採択事項)。
削除は物理削除せず deleted_at を記録する。編集は content 更新 + edited_at 記録する。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def utc_now_iso() -> str:
    """現在時刻を ISO8601 (UTC, 'Z' 終端) で返す。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class MessageRecord:
    """messages テーブル 1 行分のデータ。"""

    message_id: str
    guild_id: str
    channel_id: str
    author_id: str
    author_name: str
    ts: str
    content: str = ""
    thread_parent_id: str | None = None
    edited_at: str | None = None
    deleted_at: str | None = None
    attachments: list[Any] = field(default_factory=list)
    reply_to_id: str | None = None
    raw: dict[str, Any] | None = None
    source: str = "gateway"


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """DB へ接続し WAL モードを有効化する。呼び出し側で init_db を呼ぶこと。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """schema.sql を適用してテーブル/トリガ/FTS を作成する (冪等)。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def upsert_message(conn: sqlite3.Connection, msg: MessageRecord) -> None:
    """メッセージを挿入する。同一 message_id が既にあれば内容を上書きする (削除済みは保護)。

    dedupe は message_id (PK) のみで行う。同じ message_id を複数回 upsert しても
    1 行のまま (INSERT ... ON CONFLICT DO UPDATE)。
    """
    attachments_json = json.dumps(msg.attachments, ensure_ascii=False)
    raw_json = json.dumps(msg.raw, ensure_ascii=False) if msg.raw is not None else None
    ingested_at = utc_now_iso()

    conn.execute(
        """
        INSERT INTO messages (
            message_id, guild_id, channel_id, thread_parent_id,
            author_id, author_name, ts, edited_at, deleted_at,
            content, attachments_json, reply_to_id, raw_json,
            ingested_at, source
        ) VALUES (
            :message_id, :guild_id, :channel_id, :thread_parent_id,
            :author_id, :author_name, :ts, :edited_at, :deleted_at,
            :content, :attachments_json, :reply_to_id, :raw_json,
            :ingested_at, :source
        )
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id,
            thread_parent_id = excluded.thread_parent_id,
            author_id = excluded.author_id,
            author_name = excluded.author_name,
            ts = excluded.ts,
            content = excluded.content,
            attachments_json = excluded.attachments_json,
            reply_to_id = excluded.reply_to_id,
            raw_json = excluded.raw_json,
            ingested_at = excluded.ingested_at,
            source = excluded.source
        WHERE messages.deleted_at IS NULL
        """,
        {
            "message_id": msg.message_id,
            "guild_id": msg.guild_id,
            "channel_id": msg.channel_id,
            "thread_parent_id": msg.thread_parent_id,
            "author_id": msg.author_id,
            "author_name": msg.author_name,
            "ts": msg.ts,
            "edited_at": msg.edited_at,
            "deleted_at": msg.deleted_at,
            "content": msg.content,
            "attachments_json": attachments_json,
            "reply_to_id": msg.reply_to_id,
            "raw_json": raw_json,
            "ingested_at": ingested_at,
            "source": msg.source,
        },
    )
    conn.commit()


def mark_edited(conn: sqlite3.Connection, message_id: str, new_content: str, edited_at: str | None = None) -> None:
    """メッセージの content を更新し edited_at を記録する。存在しなければ何もしない。"""
    edited_at = edited_at or utc_now_iso()
    conn.execute(
        "UPDATE messages SET content = ?, edited_at = ? WHERE message_id = ? AND deleted_at IS NULL",
        (new_content, edited_at, message_id),
    )
    conn.commit()


def mark_deleted(conn: sqlite3.Connection, message_id: str, deleted_at: str | None = None) -> None:
    """メッセージを論理削除する (物理削除しない)。存在しなければ何もしない。"""
    deleted_at = deleted_at or utc_now_iso()
    conn.execute(
        "UPDATE messages SET deleted_at = ? WHERE message_id = ?",
        (deleted_at, message_id),
    )
    conn.commit()


def upsert_channel(
    conn: sqlite3.Connection,
    channel_id: str,
    guild_id: str,
    name: str,
    type_: str,
    parent_id: str | None = None,
    last_seen_ts: str | None = None,
) -> None:
    """チャンネル情報を挿入/更新する。"""
    last_seen_ts = last_seen_ts or utc_now_iso()
    conn.execute(
        """
        INSERT INTO channels (channel_id, guild_id, name, type, parent_id, last_seen_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            name = excluded.name,
            type = excluded.type,
            parent_id = excluded.parent_id,
            last_seen_ts = excluded.last_seen_ts
        """,
        (channel_id, guild_id, name, type_, parent_id, last_seen_ts),
    )
    conn.commit()


def get_sync_state(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any] | None:
    """チャンネルのバックフィル進捗を取得する。未着手なら None。"""
    row = conn.execute(
        "SELECT channel_id, backfill_done, oldest_id, newest_id, updated_at FROM sync_state WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def upsert_sync_state(
    conn: sqlite3.Connection,
    channel_id: str,
    backfill_done: bool,
    oldest_id: str | None = None,
    newest_id: str | None = None,
) -> None:
    """バックフィル進捗を挿入/更新する。"""
    updated_at = utc_now_iso()
    conn.execute(
        """
        INSERT INTO sync_state (channel_id, backfill_done, oldest_id, newest_id, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            backfill_done = excluded.backfill_done,
            oldest_id = excluded.oldest_id,
            newest_id = excluded.newest_id,
            updated_at = excluded.updated_at
        """,
        (channel_id, 1 if backfill_done else 0, oldest_id, newest_id, updated_at),
    )
    conn.commit()


def search_fts(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict[str, Any]]:
    """messages_fts で全文検索し、該当する messages 行を返す (削除済みは除外)。"""
    rows = conn.execute(
        """
        SELECT m.* FROM messages_fts f
        JOIN messages m ON m.rowid = f.rowid
        WHERE f.content MATCH ? AND m.deleted_at IS NULL
        ORDER BY m.ts
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_active_messages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """削除されていない全メッセージを ts 昇順で返す (export_v2 用)。"""
    rows = conn.execute(
        "SELECT * FROM messages WHERE deleted_at IS NULL ORDER BY ts ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_channels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全チャンネルを channel_id 順で返す。"""
    rows = conn.execute("SELECT * FROM channels ORDER BY channel_id ASC").fetchall()
    return [dict(r) for r in rows]
