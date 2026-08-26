#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chronica v2 データ生成パイプライン。

2026-08-23/24 の cmux フルキャプチャ (browser-structured-capture.json) を
全チャンネル横断で読み込み、ビューア用データ (chronica-data.js) を生成する。

private local only。外部送信・deploy・push は禁止。

入力:
  discord/servers/<server_id>/channels/<channel_id>/captures/<capture_dir>/browser-structured-capture.json

出力:
  chronica-data.js (window.CHRONICA_V2 = {...}; 形式)

既存の 既存のビューアとデータには
一切手を触れない (別データ系統として新規に作る)。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- パス定義 ----
SCRIPT_DIR = Path(__file__).resolve().parent
RAW_SNAPSHOTS_DIR = SCRIPT_DIR.parent
SERVER_ID = os.environ.get("CHRONICA_SERVER_ID", "")  # 収集対象の Discord サーバー ID (.env で指定)
SERVER_DIR = RAW_SNAPSHOTS_DIR / "discord" / "servers" / SERVER_ID
CHANNELS_DIR = SERVER_DIR / "channels"

OUTPUT_PATH = SCRIPT_DIR / "chronica-data.js"

SERVER_NAME = "対象サーバー"
JST = timezone(timedelta(hours=9))

# 表示名の短縮マップ (正規化後の表示名 -> 短縮名)
DISPLAY_NAME_MAP: dict[str, str] = {}
# 表示名の正規化マップ。必要なら {"元の表示名": "短縮名"} を追記する。
# 実在するハンドル名はリポジトリに含めない (SECURITY.md 参照)。


def normalize_author(raw: str) -> str:
    """author 文字列を正規化する。

    元の形式: "表示名 [タグ], サーバータグ：xxx" または "表示名" のみ。
    " [" 以降 (タグ) と ", サーバータグ" 以降を落として前後空白を除去し、
    短縮マップを適用する。
    """
    name = raw
    # ", サーバータグ" 以降を落とす (タグより先に切ってもよいが念のため両方処理)
    idx = name.find(", サーバータグ")
    if idx != -1:
        name = name[:idx]
    # " [" 以降 (タグ表記) を落とす
    idx = name.find(" [")
    if idx != -1:
        name = name[:idx]
    name = name.strip()
    return DISPLAY_NAME_MAP.get(name, name)


def to_jst_iso(ts_utc: str) -> str | None:
    """ISO8601 (UTC, 'Z' 終端) を JST (+09:00) ISO 文字列に変換する。"""
    if not ts_utc:
        return None
    # "2026-01-27T12:26:07.969Z" 形式を想定。fromisoformat は Python 3.11+ で Z を扱えるが
    # 環境差を避けるため明示的に置換する。
    s = ts_utc.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    dt_jst = dt.astimezone(JST)
    return dt_jst.isoformat()


def channel_display_name(safe_title: str, channel_id: str) -> str:
    """safe_title から「スレッド:」プレフィクスを除去する。空なら channel_id。"""
    name = safe_title or ""
    if name.startswith("スレッド:"):
        name = name[len("スレッド:") :]
    name = name.strip()
    return name if name else channel_id


def load_captures() -> list[tuple[str, dict]]:
    """全チャンネルの browser-structured-capture.json を (channel_id, data) で返す。

    1 チャンネルに複数キャプチャがある場合は、そのチャンネルディレクトリ直下の
    captures/*/browser-structured-capture.json を全て集める (呼び出し側で統合する)。
    """
    results: list[tuple[str, dict]] = []
    if not CHANNELS_DIR.exists():
        return results
    for channel_dir in sorted(CHANNELS_DIR.iterdir()):
        if not channel_dir.is_dir():
            continue
        channel_id = channel_dir.name
        captures_dir = channel_dir / "captures"
        if not captures_dir.exists():
            continue
        for capture_dir in sorted(captures_dir.iterdir()):
            cap_file = capture_dir / "browser-structured-capture.json"
            if not cap_file.is_file():
                continue
            with cap_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            results.append((channel_id, data))
    return results


def build() -> dict:
    captures = load_captures()

    # チャンネルごとに複数キャプチャを統合する
    # channel_id -> {"safe_title":..., "full": bool, "messages": {message_id: message dict}}
    channel_state: dict[str, dict] = {}
    channel_order: list[str] = []
    skipped_channels: set[str] = set()

    for channel_id, data in captures:
        if channel_id not in channel_state:
            channel_state[channel_id] = {
                "safe_title": data.get("safe_title", ""),
                "full": False,
                "messages": {},
            }
            channel_order.append(channel_id)

        state = channel_state[channel_id]
        # safe_title は最初に見つかったもの優先、空なら後続で補完
        if not state["safe_title"] and data.get("safe_title"):
            state["safe_title"] = data.get("safe_title")

        if data.get("oldest_boundary_reached") and data.get("latest_boundary_reached"):
            state["full"] = True

        for msg in data.get("messages", []):
            message_id = msg.get("message_id")
            author_raw = msg.get("author") or ""
            timestamp_raw = msg.get("timestamp") or ""
            if not message_id:
                continue
            if not author_raw or not timestamp_raw:
                # チャンネル先頭のプレースホルダ等、捨てる行
                skipped_channels.add(channel_id)
                continue
            # 同一 message_id は先着優先 (dedupe は message_id 一致のみ)
            if message_id in state["messages"]:
                continue
            state["messages"][message_id] = {
                "author": author_raw,
                "timestamp": timestamp_raw,
                "content": msg.get("content") or "",
                "attachments": msg.get("attachments") or [],
            }

    # 出力用 channels 配列を組み立てる (メッセージが 1 件も無いチャンネルも一覧には残す)
    channels_out: list[dict] = []
    channel_index: dict[str, int] = {}
    dropped_row_count = 0

    for i, channel_id in enumerate(channel_order):
        channel_index[channel_id] = i
        state = channel_state[channel_id]
        name = channel_display_name(state["safe_title"], channel_id)
        channels_out.append(
            {
                "i": i,
                "id": channel_id,
                "name": name,
                "n": 0,
                "first": None,
                "last": None,
                "full": state["full"],
            }
        )

    # messages 配列を組み立て (JST 変換 + author 正規化)
    messages_out: list[dict] = []
    for channel_id in channel_order:
        state = channel_state[channel_id]
        ci = channel_index[channel_id]
        for message_id, msg in state["messages"].items():
            ts_jst = to_jst_iso(msg["timestamp"])
            if ts_jst is None:
                dropped_row_count += 1
                continue
            author_norm = normalize_author(msg["author"])
            if not author_norm:
                dropped_row_count += 1
                continue
            messages_out.append(
                {
                    "id": message_id,
                    "ts": ts_jst,
                    "a": author_norm,
                    "c": ci,
                    "x": msg["content"],
                    "at": len(msg["attachments"]),
                    "ref": None,
                }
            )

    # 捨てた行数を集計 (プレースホルダ由来 + 変換失敗由来)
    dropped_placeholder_count = 0
    for channel_id, data in captures:
        for msg in data.get("messages", []):
            author_raw = msg.get("author") or ""
            timestamp_raw = msg.get("timestamp") or ""
            if not author_raw or not timestamp_raw:
                dropped_placeholder_count += 1

    # ts 昇順ソート
    messages_out.sort(key=lambda m: m["ts"])

    # channels の n / first / last を messages から再集計する
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

    generated_at = datetime.now(JST).isoformat()

    payload = {
        "server": SERVER_NAME,
        "generated_at": generated_at,
        "schema": "v2",
        "channels": channels_out,
        "messages": messages_out,
    }

    stats = {
        "capture_file_count": len(captures),
        "channel_count": len(channels_out),
        "message_count": len(messages_out),
        "author_count": len({m["a"] for m in messages_out}),
        "dropped_placeholder_rows": dropped_placeholder_count,
        "dropped_conversion_rows": dropped_row_count,
        "skipped_channels_with_placeholders": len(skipped_channels),
    }
    if messages_out:
        stats["period_first"] = messages_out[0]["ts"]
        stats["period_last"] = messages_out[-1]["ts"]
    else:
        stats["period_first"] = None
        stats["period_last"] = None

    return payload, stats


def self_check(payload: dict) -> None:
    """セルフチェック: message_id ユニーク性 / ts 昇順 / c の範囲。"""
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


def main() -> None:
    payload, stats = build()
    self_check(payload)

    js = "window.CHRONICA_V2 = " + json.dumps(payload, ensure_ascii=False, indent=None) + ";\n"
    OUTPUT_PATH.write_text(js, encoding="utf-8")

    size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)

    print("=== Chronica v2 生成結果 ===")
    print(f"capture file 数: {stats['capture_file_count']}")
    print(f"チャンネル数: {stats['channel_count']}")
    print(f"総メッセージ件数: {stats['message_count']}")
    print(f"発言者数 (正規化後 unique): {stats['author_count']}")
    print(f"期間: {stats['period_first']} 〜 {stats['period_last']}")
    print(f"捨てた行数 (プレースホルダ等): {stats['dropped_placeholder_rows']}")
    print(f"捨てた行数 (JST変換/author 失敗): {stats['dropped_conversion_rows']}")
    print(f"プレースホルダを含んでいたチャンネル数: {stats['skipped_channels_with_placeholders']}")
    print(f"出力ファイル: {OUTPUT_PATH}")
    print(f"出力サイズ: {size_mb:.3f} MB")
    print("セルフチェック: OK (id unique / ts昇順+09:00終端 / c範囲内)")


if __name__ == "__main__":
    main()
