#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py / export_v2.py のユニットテスト。

discord.py を import しない層 (store / export_v2) だけを対象にし、実 Discord 接続なしで
`python test_store.py` として自走できる assert 形式のテストにする。

対象:
    - upsert_message の冪等性 (同 message_id 2回で1行)
    - 編集反映 (mark_edited)
    - 削除マーク (mark_deleted, 物理削除しない)
    - FTS 検索
    - export_v2 の JSON 構造検証 (ts 昇順 / ref 解決 / deleted 除外)
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_v2
import store

PASS_COUNT = 0


def check(condition: bool, label: str) -> None:
    """assert 相当。PASS/FAIL を明示的に print する。"""
    global PASS_COUNT
    if not condition:
        raise AssertionError(f"FAIL: {label}")
    PASS_COUNT += 1
    print(f"PASS: {label}")


def make_message(
    message_id: str,
    channel_id: str = "ch1",
    author_id: str = "u1",
    author_name: str = "テスト太郎",
    ts: str = "2026-08-01T00:00:00+00:00",
    content: str = "hello",
    reply_to_id: str | None = None,
) -> store.MessageRecord:
    return store.MessageRecord(
        message_id=message_id,
        guild_id="g1",
        channel_id=channel_id,
        author_id=author_id,
        author_name=author_name,
        ts=ts,
        content=content,
        reply_to_id=reply_to_id,
        attachments=[],
        raw={"id": message_id},
    )


def test_upsert_idempotency(db_path: str) -> None:
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("m1", content="v1"))
    store.upsert_message(conn, make_message("m1", content="v2"))

    rows = conn.execute("SELECT * FROM messages WHERE message_id = ?", ("m1",)).fetchall()
    check(len(rows) == 1, "upsert_message: 同一 message_id 2回で1行のみ")
    check(rows[0]["content"] == "v2", "upsert_message: 2回目の内容で上書きされる")

    conn.close()


def test_mark_edited(db_path: str) -> None:
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("m2", content="original"))
    store.mark_edited(conn, "m2", "edited content", edited_at="2026-08-01T01:00:00+00:00")

    row = conn.execute("SELECT * FROM messages WHERE message_id = ?", ("m2",)).fetchone()
    check(row["content"] == "edited content", "mark_edited: content が更新される")
    check(row["edited_at"] == "2026-08-01T01:00:00+00:00", "mark_edited: edited_at が記録される")

    conn.close()


def test_mark_deleted(db_path: str) -> None:
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("m3", content="to be deleted"))
    store.mark_deleted(conn, "m3", deleted_at="2026-08-01T02:00:00+00:00")

    row = conn.execute("SELECT * FROM messages WHERE message_id = ?", ("m3",)).fetchone()
    check(row is not None, "mark_deleted: 物理削除しない (行は残る)")
    check(row["deleted_at"] == "2026-08-01T02:00:00+00:00", "mark_deleted: deleted_at が記録される")
    check(row["content"] == "to be deleted", "mark_deleted: content はそのまま残る")

    active = store.fetch_active_messages(conn)
    check(all(m["message_id"] != "m3" for m in active), "fetch_active_messages: 削除済みは除外される")

    conn.close()


def test_fts_search(db_path: str) -> None:
    conn = store.get_connection(db_path)
    store.init_db(conn)

    # tokenize='trigram' は 3文字未満のクエリにヒットしない制約があるため、
    # 検索語は3文字以上にする ("世界です")
    store.upsert_message(conn, make_message("m4", content="こんにちは世界です"))
    store.upsert_message(conn, make_message("m5", content="さようなら世界です"))
    store.upsert_message(conn, make_message("m6", content="無関係な内容"))

    results = store.search_fts(conn, "世界です")
    ids = {r["message_id"] for r in results}
    check(ids == {"m4", "m5"}, "search_fts: 該当する2件がヒットする")

    # 削除したら検索から除外されることも確認する
    store.mark_deleted(conn, "m4")
    results2 = store.search_fts(conn, "世界です")
    ids2 = {r["message_id"] for r in results2}
    check(ids2 == {"m5"}, "search_fts: 削除済みメッセージは除外される")

    conn.close()


def test_export_v2_structure(db_path: str, out_path: str) -> None:
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_channel(conn, "chA", "g1", "雑談", "text")
    store.upsert_channel(conn, "chB", "g1", "お知らせ", "text")

    # ts はわざと逆順で投入し、export 後に昇順になることを確認する
    store.upsert_message(
        conn,
        make_message("e3", channel_id="chA", ts="2026-08-01T03:00:00+00:00", content="3番目"),
    )
    store.upsert_message(
        conn,
        make_message("e1", channel_id="chA", ts="2026-08-01T01:00:00+00:00", content="1番目"),
    )
    store.upsert_message(
        conn,
        make_message(
            "e2",
            channel_id="chB",
            ts="2026-08-01T02:00:00+00:00",
            content="返信",
            reply_to_id="e1",
        ),
    )
    # 削除済みメッセージは出力に含まれないことを確認する
    store.upsert_message(
        conn,
        make_message("e4", channel_id="chA", ts="2026-08-01T04:00:00+00:00", content="削除される"),
    )
    store.mark_deleted(conn, "e4")

    payload = export_v2.export_to_js(conn, out_path, server_name="テストサーバー")

    check(payload["schema"] == "v2", "export_v2: schema が v2")
    check(len(payload["messages"]) == 3, "export_v2: 削除済みを除いた3件が出力される")

    ts_list = [m["ts"] for m in payload["messages"]]
    check(ts_list == sorted(ts_list), "export_v2: ts が昇順")
    check(all(t.endswith("+09:00") for t in ts_list), "export_v2: ts が +09:00 終端 (JST)")

    ids_in_order = [m["id"] for m in payload["messages"]]
    check(ids_in_order == ["e1", "e2", "e3"], "export_v2: message id が ts 昇順で並ぶ")

    e2 = next(m for m in payload["messages"] if m["id"] == "e2")
    check(e2["ref"] == "e1", "export_v2: ref に reply_to_id がそのまま入る")
    check(any(m["id"] == e2["ref"] for m in payload["messages"]), "export_v2: ref が実在する message id を指す")

    e4_present = any(m["id"] == "e4" for m in payload["messages"])
    check(not e4_present, "export_v2: 削除済みメッセージ (e4) は出力に含まれない")

    check(len(payload["channels"]) == 2, "export_v2: チャンネルが2件出力される")
    ch_by_id = {c["id"]: c for c in payload["channels"]}
    check(ch_by_id["chA"]["n"] == 2, "export_v2: chA のメッセージ数が正しい")
    check(ch_by_id["chB"]["n"] == 1, "export_v2: chB のメッセージ数が正しい")

    # 実際に書き出した .js ファイルが window.CHRONICA_V2 = {...}; 形式であることを確認する
    js_text = Path(out_path).read_text(encoding="utf-8")
    check(js_text.startswith("window.CHRONICA_V2 = "), "export_v2: 出力ファイルの先頭が契約通り")
    check(js_text.rstrip().endswith(";"), "export_v2: 出力ファイルがセミコロン終端")
    json_body = js_text[len("window.CHRONICA_V2 = ") :].rstrip().rstrip(";")
    parsed = json.loads(json_body)
    check(parsed["server"] == "テストサーバー", "export_v2: 出力ファイルが JSON として parse でき server 名が一致")

    conn.close()


def main() -> None:
    # Windows では sqlite の WAL 補助ファイルがハンドル解放直後でも残ることがあり、
    # TemporaryDirectory の自動削除 (strict) が PermissionError になる場合がある。
    # そのため手動で作成し、後始末は ignore_errors=True で行う。
    tmpdir = tempfile.mkdtemp(prefix="chronica_test_")
    try:
        test_upsert_idempotency(os.path.join(tmpdir, "t1.db"))
        test_mark_edited(os.path.join(tmpdir, "t2.db"))
        test_mark_deleted(os.path.join(tmpdir, "t3.db"))
        test_fts_search(os.path.join(tmpdir, "t4.db"))
        test_export_v2_structure(
            os.path.join(tmpdir, "t5.db"),
            os.path.join(tmpdir, "chronica-v2-data.js"),
        )
    finally:
        gc.collect()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n=== {PASS_COUNT} assertions PASSED ===")


if __name__ == "__main__":
    main()
