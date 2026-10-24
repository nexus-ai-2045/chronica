#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite (store.py の管理する chronica.db) から chronica-data.js を生成する。

出力契約は pipeline/build_chronica_v2.py と完全互換:
    window.CHRONICA_V2 = {
        server, generated_at, schema: "v2",
        channels: [{i, id, name, n, first, last, full}],
        messages: [{id, ts(+09:00 昇順), a, c, x, at, ref}],
    };

- deleted_at が付いたメッセージは除外する。
- ref には reply_to_id をそのまま入れる (ビューアの返信弧はこの id が
  messages[].id と一致することを前提にしている)。
- author 正規化は pipeline/build_chronica_v2.py の normalize_author を移植したもの
  (pipeline 側のファイルは変更しない)。

discord.py に依存しない (テストで実 Discord なしに検証できるようにするため)。
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import envutil
import store

JST = timezone(timedelta(hours=9))

# 表示名の短縮マップ (pipeline/build_chronica_v2.py と同一。移植元は変更しない)
DISPLAY_NAME_MAP: dict[str, str] = {}
# 表示名の正規化マップ。必要なら {"元の表示名": "短縮名"} を追記する。
# 実在するハンドル名はリポジトリに含めない (SECURITY.md 参照)。


def normalize_author(raw: str) -> str:
    """author 文字列を正規化する (pipeline/build_chronica_v2.py から移植)。

    元の形式: "表示名 [タグ], サーバータグ：xxx" または "表示名" のみ。
    " [" 以降 (タグ) と ", サーバータグ" 以降を落として前後空白を除去し、
    短縮マップを適用する。
    """
    name = raw
    idx = name.find(", サーバータグ")
    if idx != -1:
        name = name[:idx]
    idx = name.find(" [")
    if idx != -1:
        name = name[:idx]
    name = name.strip()
    return DISPLAY_NAME_MAP.get(name, name)


def to_jst_iso(ts_raw: str) -> str | None:
    """ISO8601 (UTC/オフセット付き) を JST (+09:00) ISO 文字列に変換する。

    discord.py の isoformat() は 'Z' ではなく '+00:00' を使うが、念のため
    'Z' 終端も許容する。
    """
    if not ts_raw:
        return None
    s = ts_raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_jst = dt.astimezone(JST)
    return dt_jst.isoformat()


def build_payload(conn: Any, server_name: str) -> dict[str, Any]:
    """DB から CHRONICA_V2 payload を組み立てる。"""
    channel_rows = store.fetch_channels(conn)
    message_rows = store.fetch_active_messages(conn)

    # チャンネル一覧に無いが messages 側にだけ出てくる channel_id を拾うための集合
    known_channel_ids = {row["channel_id"] for row in channel_rows}
    extra_channel_ids = sorted(
        {m["channel_id"] for m in message_rows if m["channel_id"] not in known_channel_ids}
    )

    channel_order: list[str] = [row["channel_id"] for row in channel_rows] + extra_channel_ids
    channel_index: dict[str, int] = {cid: i for i, cid in enumerate(channel_order)}
    name_by_id = {row["channel_id"]: row["name"] for row in channel_rows}

    channels_out: list[dict[str, Any]] = []
    for i, cid in enumerate(channel_order):
        channels_out.append(
            {
                "i": i,
                "id": cid,
                "name": name_by_id.get(cid, cid),
                "n": 0,
                "first": None,
                "last": None,
                "full": False,
            }
        )

    messages_out: list[dict[str, Any]] = []
    for row in message_rows:
        ts_jst = to_jst_iso(row["ts"])
        if ts_jst is None:
            continue
        author_norm = normalize_author(row["author_name"] or "")
        if not author_norm:
            continue
        attachments = json.loads(row["attachments_json"] or "[]")
        messages_out.append(
            {
                "id": row["message_id"],
                "ts": ts_jst,
                "a": author_norm,
                "c": channel_index[row["channel_id"]],
                "x": row["content"] or "",
                "at": len(attachments),
                "ref": row["reply_to_id"],
            }
        )

    messages_out.sort(key=lambda m: m["ts"])

    for ch in channels_out:
        ch["n"] = 0
        ch["first"] = None
        ch["last"] = None
    for m in messages_out:
        ch = channels_out[m["c"]]
        ch["n"] += 1
        if ch["first"] is None or m["ts"] < ch["first"]:
            ch["first"] = m["ts"]
        if ch["last"] is None or m["ts"] > ch["last"]:
            ch["last"] = m["ts"]

    # full フラグ: sync_state.backfill_done が真のチャンネルのみ True
    for cid in channel_order:
        state = store.get_sync_state(conn, cid)
        if state and state.get("backfill_done"):
            channels_out[channel_index[cid]]["full"] = True

    generated_at = datetime.now(JST).isoformat()

    return {
        "server": server_name,
        "generated_at": generated_at,
        "schema": "v2",
        "channels": channels_out,
        "messages": messages_out,
    }


def self_check(payload: dict[str, Any]) -> None:
    """セルフチェック: message_id ユニーク性 / ts 昇順 / c の範囲 (pipeline と同等)。"""
    messages = payload["messages"]
    channels = payload["channels"]

    ids = [m["id"] for m in messages]
    assert len(ids) == len(set(ids)), "message_id に重複がある"

    for m in messages:
        assert m["ts"].endswith("+09:00"), f"ts が +09:00 終端でない: {m['ts']}"

    ts_list = [m["ts"] for m in messages]
    assert ts_list == sorted(ts_list), "ts が昇順でない"

    n_channels = len(channels)
    for m in messages:
        assert 0 <= m["c"] < n_channels, f"c がチャンネル範囲外: {m['c']}"


def export_to_js(conn: Any, out_path: str | Path, server_name: str) -> dict[str, Any]:
    """payload を組み立てて chronica-data.js を書き出す。呼び出し側テスト用に payload も返す。"""
    payload = build_payload(conn, server_name)
    self_check(payload)
    js = "window.CHRONICA_V2 = " + json.dumps(payload, ensure_ascii=False, indent=None) + ";\n"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(js, encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronica v2 データ生成 (bot DB -> chronica-data.js)")
    parser.add_argument("--server-name", default=None, help="出力 payload の server 名 (未指定なら CHRONICA_SERVER_NAME env か既定値)")
    args = parser.parse_args()

    envutil.load_dotenv()
    db_path = envutil.get_db_path()
    out_path = envutil.get_export_out_path()
    server_name = args.server_name or os.environ.get("CHRONICA_SERVER_NAME", "対象サーバー")

    conn = store.get_connection(db_path)
    store.init_db(conn)
    payload = export_to_js(conn, out_path, server_name)

    print("=== Chronica v2 (bot) 生成結果 ===")
    print(f"チャンネル数: {len(payload['channels'])}")
    print(f"総メッセージ件数: {len(payload['messages'])}")
    print(f"出力ファイル: {out_path}")
    print("セルフチェック: OK (id unique / ts昇順+09:00終端 / c範囲内)")


if __name__ == "__main__":
    main()
