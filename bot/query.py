#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chronica DB の検索/取得 CLI。

ADR 0001 (日本語ハイブリッド全文検索) / ADR 0002 (AI に DB をコンテキストとして
渡す用途) で想定されていた「実際に引ける入口」を提供する。discord.py に依存しない
(store.py の方針を踏襲。envutil は使ってよい)。

サブコマンド:
    search    <クエリ> [--channel] [--since] [--until] [--limit] [--json]
    window    --channel <id|name> [--since] [--until] [--limit] [--json]
    channels  [--json]
    stats     [--json]

各サブコマンドの中核処理は関数として独立させてあり (search / window /
list_channels / get_stats)、CLI を経由せずテストから直接呼び出せる。

使い方:
    python query.py search ハッカソン --channel 雑談 --limit 20
    python query.py window --channel 123456789 --since 2026-08-01T00:00:00Z
    python query.py channels --json
    python query.py stats
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import envutil
import store


class ChannelResolutionError(ValueError):
    """--channel 引数を channel_id に解決できなかった場合の例外。"""


def resolve_channel_id(conn: sqlite3.Connection, channel_arg: str) -> str:
    """--channel 引数 (channel_id または名前の部分一致) を channel_id に解決する。

    優先順位:
      1. channels テーブルの channel_id 完全一致
      2. messages テーブルに直接その channel_id が存在する (channels 未登録でも許容)
      3. channels.name の部分一致 (大小無視)。1件に絞れない場合は例外。
    """
    channel_arg = channel_arg.strip()
    if not channel_arg:
        raise ChannelResolutionError("--channel が空です")

    row = conn.execute(
        "SELECT channel_id FROM channels WHERE channel_id = ?", (channel_arg,)
    ).fetchone()
    if row is not None:
        return row["channel_id"]

    row = conn.execute(
        "SELECT DISTINCT channel_id FROM messages WHERE channel_id = ? LIMIT 1", (channel_arg,)
    ).fetchone()
    if row is not None:
        return row["channel_id"]

    like_pattern = "%" + store._escape_like_pattern(channel_arg) + "%"
    rows = conn.execute(
        f"SELECT channel_id, name FROM channels WHERE name LIKE ? ESCAPE '{store._LIKE_ESCAPE_CHAR}' COLLATE NOCASE",
        (like_pattern,),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["channel_id"]
    if len(rows) == 0:
        raise ChannelResolutionError(f"チャンネルが見つかりません: {channel_arg!r}")
    candidates = ", ".join(f"{r['name']}({r['channel_id']})" for r in rows)
    raise ChannelResolutionError(f"チャンネル名 {channel_arg!r} が複数に一致します: {candidates}")


def search(
    conn: sqlite3.Connection,
    query_text: str,
    channel: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """全文検索する (store.search_fts を使い、channel/since/until で追加絞り込みする)。"""
    channel_id = resolve_channel_id(conn, channel) if channel else None

    # channel/since/until の絞り込みで件数が減る分、内部取得件数は多めに取っておく。
    needs_extra = channel_id is not None or since is not None or until is not None
    fetch_limit = max(limit * 20, 500) if needs_extra else limit
    rows = store.search_fts(conn, query_text, limit=fetch_limit)

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if channel_id is not None and row["channel_id"] != channel_id:
            continue
        if since is not None and row["ts"] < since:
            continue
        if until is not None and row["ts"] > until:
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def window(
    conn: sqlite3.Connection,
    channel: str,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """指定チャンネルの期間スライスを ts 昇順で返す (要約入力取得用途)。"""
    channel_id = resolve_channel_id(conn, channel)

    sql = "SELECT * FROM messages WHERE channel_id = ? AND deleted_at IS NULL"
    params: list[Any] = [channel_id]
    if since is not None:
        sql += " AND ts >= ?"
        params.append(since)
    if until is not None:
        sql += " AND ts <= ?"
        params.append(until)
    sql += " ORDER BY ts ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    messages = [dict(r) for r in rows]
    approx_chars = sum(len(m.get("content") or "") for m in messages)

    return {
        "channel_id": channel_id,
        "count": len(messages),
        "approx_chars": approx_chars,
        "messages": messages,
    }


def list_channels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """チャンネル一覧 (id / name / 件数 / 期間) を返す。

    channels テーブル未登録でも messages に channel_id が存在すれば一覧に含める
    (その場合 name は channel_id をそのまま使う)。
    """
    registered = {
        r["channel_id"]: r
        for r in conn.execute("SELECT channel_id, name, type FROM channels").fetchall()
    }
    message_channel_ids = {
        r["channel_id"]
        for r in conn.execute("SELECT DISTINCT channel_id FROM messages").fetchall()
    }
    all_ids = set(registered.keys()) | message_channel_ids

    result: list[dict[str, Any]] = []
    for channel_id in all_ids:
        stat_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt, MIN(ts) AS first_ts, MAX(ts) AS last_ts
            FROM messages WHERE channel_id = ? AND deleted_at IS NULL
            """,
            (channel_id,),
        ).fetchone()
        reg = registered.get(channel_id)
        result.append(
            {
                "channel_id": channel_id,
                "name": reg["name"] if reg is not None else channel_id,
                "type": reg["type"] if reg is not None else None,
                "count": stat_row["cnt"],
                "first_ts": stat_row["first_ts"],
                "last_ts": stat_row["last_ts"],
            }
        )
    result.sort(key=lambda c: (c["name"] or ""))
    return result


def get_stats(conn: sqlite3.Connection, db_path: str | Path | None = None) -> dict[str, Any]:
    """総件数 / 発言者数 / チャンネル数 / 期間 / DB サイズを返す。"""
    total_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM messages WHERE deleted_at IS NULL"
    ).fetchone()
    author_row = conn.execute(
        "SELECT COUNT(DISTINCT author_id) AS cnt FROM messages WHERE deleted_at IS NULL"
    ).fetchone()
    period_row = conn.execute(
        "SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM messages WHERE deleted_at IS NULL"
    ).fetchone()
    channel_count = len(list_channels(conn))

    db_size_bytes: int | None = None
    if db_path is not None and Path(db_path).is_file():
        db_size_bytes = Path(db_path).stat().st_size
    else:
        # ファイルパスが分からない場合 (:memory: 等) は page 数から概算する。
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        db_size_bytes = int(page_count) * int(page_size)

    return {
        "message_count": total_row["cnt"],
        "author_count": author_row["cnt"],
        "channel_count": channel_count,
        "period_first": period_row["first_ts"],
        "period_last": period_row["last_ts"],
        "db_size_bytes": db_size_bytes,
    }


# --- ここから CLI 層 (引数処理・出力整形のみ。中核処理は上記の関数を呼ぶだけ) ---


def _print_messages_human(messages: list[dict[str, Any]]) -> None:
    for m in messages:
        print(f"[{m['ts']}] ({m['channel_id']}) {m['author_name']}: {m['content']}")


def _open_conn(db_arg: str | None) -> tuple[sqlite3.Connection, str]:
    envutil.load_dotenv()
    db_path = db_arg or envutil.get_db_path()
    conn = store.get_connection(db_path)
    store.init_db(conn)
    return conn, db_path


def cmd_search(args: argparse.Namespace) -> int:
    conn, _ = _open_conn(args.db)
    try:
        results = search(
            conn,
            args.query,
            channel=args.channel,
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
    except ChannelResolutionError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_messages_human(results)
        print(f"\n{len(results)} 件")
    return 0


def cmd_window(args: argparse.Namespace) -> int:
    conn, _ = _open_conn(args.db)
    try:
        result = window(conn, args.channel, since=args.since, until=args.until, limit=args.limit)
    except ChannelResolutionError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_messages_human(result["messages"])
        print(f"\n{result['count']} 件 / 概算 {result['approx_chars']} 文字")
    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    conn, _ = _open_conn(args.db)
    try:
        results = list_channels(conn)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for c in results:
            print(
                f"{c['channel_id']}\t{c['name']}\t{c['count']}件\t"
                f"{c['first_ts']} 〜 {c['last_ts']}"
            )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn, db_path = _open_conn(args.db)
    try:
        result = get_stats(conn, db_path=db_path)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"総件数: {result['message_count']}")
        print(f"発言者数: {result['author_count']}")
        print(f"チャンネル数: {result['channel_count']}")
        print(f"期間: {result['period_first']} 〜 {result['period_last']}")
        print(f"DB サイズ: {result['db_size_bytes']} bytes")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chronica DB 検索/取得 CLI")
    parser.add_argument("--db", default=None, help="DB パス (未指定なら .env の CHRONICA_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="全文検索する")
    p_search.add_argument("query", help="検索クエリ")
    p_search.add_argument("--channel", default=None, help="チャンネル id または名前 (部分一致)")
    p_search.add_argument("--since", default=None, help="開始時刻 (ISO8601)")
    p_search.add_argument("--until", default=None, help="終了時刻 (ISO8601)")
    p_search.add_argument("--limit", type=int, default=50, help="最大件数 (既定 50)")
    p_search.add_argument("--json", action="store_true", help="JSON で出力する")
    p_search.set_defaults(func=cmd_search)

    p_window = sub.add_parser("window", help="チャンネル×期間で時系列に取得する")
    p_window.add_argument("--channel", required=True, help="チャンネル id または名前 (部分一致)")
    p_window.add_argument("--since", default=None, help="開始時刻 (ISO8601)")
    p_window.add_argument("--until", default=None, help="終了時刻 (ISO8601)")
    p_window.add_argument("--limit", type=int, default=None, help="最大件数 (既定: 無制限)")
    p_window.add_argument("--json", action="store_true", help="JSON で出力する")
    p_window.set_defaults(func=cmd_window)

    p_channels = sub.add_parser("channels", help="チャンネル一覧を表示する")
    p_channels.add_argument("--json", action="store_true", help="JSON で出力する")
    p_channels.set_defaults(func=cmd_channels)

    p_stats = sub.add_parser("stats", help="全体統計を表示する")
    p_stats.add_argument("--json", action="store_true", help="JSON で出力する")
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
