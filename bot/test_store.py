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
import query
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


def test_search_hybrid_short_query(db_path: str) -> None:
    """2文字以下のクエリは LIKE 経路に落ちてもヒットすること (ADR 0001)。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("h1", content="日本語の開発を進める"))
    store.upsert_message(conn, make_message("h2", content="別の話題です"))

    # 2文字クエリ ("開発") は trigram に乗らないため LIKE 経路になる
    results = store.search_fts(conn, "開発")
    ids = {r["message_id"] for r in results}
    check(ids == {"h1"}, "search_fts: 2文字クエリ (LIKE経路) でもヒットする")

    conn.close()


def test_search_hybrid_long_query(db_path: str) -> None:
    """3文字以上のクエリは従来通り FTS 経路でヒットすること。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("l1", content="ハッカソンに参加した"))
    store.upsert_message(conn, make_message("l2", content="無関係な内容"))

    results = store.search_fts(conn, "ハッカソン")
    ids = {r["message_id"] for r in results}
    check(ids == {"l1"}, "search_fts: 3文字以上クエリ (FTS経路) でもヒットする")

    conn.close()


def test_search_like_wildcard_escape(db_path: str) -> None:
    """LIKE 経路で % を含むクエリがワイルドカードとして誤解釈されないこと。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("w1", content="進捗50%です"))
    store.upsert_message(conn, make_message("w2", content="無関係な内容その1"))

    # "0%" は2文字なので LIKE 経路。エスケープしないと全行にマッチしてしまう
    results = store.search_fts(conn, "0%")
    ids = {r["message_id"] for r in results}
    check(ids == {"w1"}, "search_fts: LIKE経路で % がリテラルとして扱われる (w1のみヒット)")

    conn.close()


def test_search_like_excludes_deleted(db_path: str) -> None:
    """LIKE 経路でも削除済みメッセージが除外されること (FTS経路と揃える)。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("d1", content="開発中の機能"))
    store.mark_deleted(conn, "d1")

    results = store.search_fts(conn, "開発")
    ids = {r["message_id"] for r in results}
    check("d1" not in ids, "search_fts: LIKE経路でも削除済みは除外される")

    conn.close()


def test_compute_input_hash(db_path: str) -> None:
    """同じメッセージ集合なら順序が違っても同じハッシュになること。"""
    h1 = store.compute_input_hash(["m1", "m2", "m3"])
    h2 = store.compute_input_hash(["m3", "m1", "m2"])
    h3 = store.compute_input_hash(["m1", "m2", "m4"])

    check(h1 == h2, "compute_input_hash: 順序が違っても同じ集合なら同じ値")
    check(h1 != h3, "compute_input_hash: 集合が違えば異なる値")


def test_summary_cache(db_path: str) -> None:
    """input_hash + model + prompt_version が同じなら二重挿入されないこと。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    input_hash = store.compute_input_hash(["m1", "m2"])
    s1 = store.upsert_summary(
        conn,
        scope_kind="channel_period",
        channel_id="ch1",
        input_hash=input_hash,
        input_message_count=2,
        summary_text="要約その1",
        model="model-a",
        prompt_version="v1",
        period_start="2026-08-01T00:00:00Z",
        period_end="2026-08-02T00:00:00Z",
    )
    s2 = store.upsert_summary(
        conn,
        scope_kind="channel_period",
        channel_id="ch1",
        input_hash=input_hash,
        input_message_count=2,
        summary_text="別のテキストを渡しても再利用される",
        model="model-a",
        prompt_version="v1",
        period_start="2026-08-01T00:00:00Z",
        period_end="2026-08-02T00:00:00Z",
    )

    check(s1["summary_id"] == s2["summary_id"], "upsert_summary: 同じキャッシュキーなら二重挿入されない")
    check(s2["summary_text"] == "要約その1", "upsert_summary: 既存の summary_text が再利用される")

    rows = conn.execute("SELECT * FROM summaries WHERE input_hash = ?", (input_hash,)).fetchall()
    check(len(rows) == 1, "upsert_summary: summaries テーブルに1行のみ")

    found = store.find_summary(conn, input_hash, "model-a", "v1")
    check(found is not None and found["summary_id"] == s1["summary_id"], "find_summary: キャッシュキーで参照できる")

    not_found = store.find_summary(conn, input_hash, "model-b", "v1")
    check(not_found is None, "find_summary: model が違えばヒットしない")

    conn.close()


def test_topics_silver_gold_and_adjudication(db_path: str) -> None:
    """silver / gold の共存と adjudication の記録を確認する。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("t1", content="話題A その1"))
    store.upsert_message(conn, make_message("t2", content="話題A その2"))

    silver_id = store.upsert_topic(
        conn,
        channel_id="ch1",
        label="機械分割の話題A",
        origin="silver",
        starts_at="2026-08-01T00:00:00Z",
        ends_at="2026-08-01T01:00:00Z",
        algorithm="texttiling",
        algorithm_version="v1",
    )
    gold_id = store.upsert_topic(
        conn,
        channel_id="ch1",
        label="人手確定の話題A",
        origin="gold",
        starts_at="2026-08-01T00:00:00Z",
        ends_at="2026-08-01T01:00:00Z",
    )

    store.link_topic_messages(conn, silver_id, ["t1", "t2"])
    store.link_topic_messages(conn, gold_id, ["t1", "t2"])
    # 冪等性確認 (同じ組を再度 link しても増えない)
    store.link_topic_messages(conn, silver_id, ["t1"])

    rows = conn.execute(
        "SELECT origin FROM topics WHERE topic_id IN (?, ?)", (silver_id, gold_id)
    ).fetchall()
    origins = {r["origin"] for r in rows}
    check(origins == {"silver", "gold"}, "topics: silver と gold が共存できる")

    link_rows = conn.execute(
        "SELECT * FROM topic_messages WHERE topic_id = ?", (silver_id,)
    ).fetchall()
    check(len(link_rows) == 2, "link_topic_messages: 冪等 (重複挿入されない)")

    adj_id = store.record_adjudication(
        conn,
        silver_topic_id=silver_id,
        action="accept",
        gold_topic_id=gold_id,
        note="機械分割をそのまま採用",
    )
    adj_row = conn.execute(
        "SELECT * FROM adjudications WHERE adjudication_id = ?", (adj_id,)
    ).fetchone()
    check(adj_row is not None, "record_adjudication: 記録される")
    check(adj_row["action"] == "accept", "record_adjudication: action が記録される")
    check(adj_row["gold_topic_id"] == gold_id, "record_adjudication: gold_topic_id が記録される")

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


def test_query_channels_and_stats(db_path: str) -> None:
    """query.list_channels / query.get_stats が関数として直接呼べること。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_channel(conn, "chA", "g1", "雑談部屋", "text")
    store.upsert_message(conn, make_message("q1", channel_id="chA", ts="2026-08-01T00:00:00Z"))
    store.upsert_message(conn, make_message("q2", channel_id="chA", ts="2026-08-02T00:00:00Z"))

    channels = query.list_channels(conn)
    check(any(c["channel_id"] == "chA" and c["count"] == 2 for c in channels), "list_channels: チャンネルごとの件数が正しい")

    stats = query.get_stats(conn, db_path=db_path)
    check(stats["message_count"] == 2, "get_stats: 総件数が正しい")
    check(stats["channel_count"] >= 1, "get_stats: チャンネル数が1以上")
    check(stats["db_size_bytes"] is not None and stats["db_size_bytes"] > 0, "get_stats: DBサイズが取得できる")

    conn.close()


def test_query_window(db_path: str) -> None:
    """query.window がチャンネル×期間で絞り込み、時系列順に返し、削除済みを除くこと。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(conn, make_message("w_a", channel_id="chW", ts="2026-08-01T03:00:00Z", content="3番目"))
    store.upsert_message(conn, make_message("w_b", channel_id="chW", ts="2026-08-01T01:00:00Z", content="1番目"))
    store.upsert_message(conn, make_message("w_c", channel_id="chW", ts="2026-08-01T02:00:00Z", content="2番目"))
    # 別チャンネルは混ざらないこと
    store.upsert_message(conn, make_message("w_other", channel_id="chOther", ts="2026-08-01T01:30:00Z"))
    # 削除済みは除外されること
    store.upsert_message(conn, make_message("w_deleted", channel_id="chW", ts="2026-08-01T01:45:00Z"))
    store.mark_deleted(conn, "w_deleted")

    result = query.window(conn, "chW")
    ids = [m["message_id"] for m in result["messages"]]
    check(ids == ["w_b", "w_c", "w_a"], "query.window: チャンネルで絞り込み時系列(ts昇順)で返る")
    check(result["count"] == 3, "query.window: count がメッセージ数と一致する")
    check(result["approx_chars"] == sum(len(c) for c in ["1番目", "2番目", "3番目"]), "query.window: approx_chars が概算文字数と一致する")
    check("w_deleted" not in ids, "query.window: 削除済みは除外される")
    check("w_other" not in ids, "query.window: 別チャンネルは混ざらない")

    result_ranged = query.window(conn, "chW", since="2026-08-01T01:30:00Z", until="2026-08-01T02:30:00Z")
    ids_ranged = [m["message_id"] for m in result_ranged["messages"]]
    check(ids_ranged == ["w_c"], "query.window: since/until で絞り込める")

    conn.close()


def test_query_search_with_filters(db_path: str) -> None:
    """query.search が --channel / --since を併用して絞り込めること。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)

    store.upsert_message(
        conn, make_message("s_a", channel_id="chS1", ts="2026-08-01T00:00:00Z", content="開発の進め方について")
    )
    store.upsert_message(
        conn, make_message("s_b", channel_id="chS2", ts="2026-08-02T00:00:00Z", content="開発の相談です")
    )
    store.upsert_message(
        conn, make_message("s_c", channel_id="chS1", ts="2026-08-03T00:00:00Z", content="開発合宿の計画")
    )

    all_hits = query.search(conn, "開発")
    check({r["message_id"] for r in all_hits} == {"s_a", "s_b", "s_c"}, "query.search: フィルタ無しで全件ヒットする")

    channel_hits = query.search(conn, "開発", channel="chS1")
    check({r["message_id"] for r in channel_hits} == {"s_a", "s_c"}, "query.search: --channel 相当の絞り込みが効く")

    since_hits = query.search(conn, "開発", channel="chS1", since="2026-08-02T00:00:00Z")
    check({r["message_id"] for r in since_hits} == {"s_c"}, "query.search: --channel と --since の併用が効く")

    conn.close()


def test_query_resolve_channel_by_name(db_path: str) -> None:
    """query.resolve_channel_id が名前の部分一致で解決できること。"""
    conn = store.get_connection(db_path)
    store.init_db(conn)
    store.upsert_channel(conn, "chR", "g1", "開発雑談", "text")

    resolved = query.resolve_channel_id(conn, "雑談")
    check(resolved == "chR", "resolve_channel_id: 名前の部分一致で解決できる")

    try:
        query.resolve_channel_id(conn, "存在しないチャンネル")
        check(False, "resolve_channel_id: 未知の名前は例外になる")
    except query.ChannelResolutionError:
        check(True, "resolve_channel_id: 未知の名前は例外になる")

    conn.close()


def test_build_raw_payload() -> None:
    """collector.build_raw_payload が専用列に無い情報を含み、credential 様の値を含まないこと。"""
    import collector

    class _FakeEmbed:
        def to_dict(self) -> dict:
            return {"title": "テスト埋め込み"}

    class _FakeEmoji:
        def __str__(self) -> str:
            return "👍"

    class _FakeReaction:
        emoji = _FakeEmoji()
        count = 3

    class _FakeUser:
        id = 111

    class _FakeFlags:
        value = 8

    class _FakeType:
        name = "default"

    class _FakeStickerFormat:
        name = "png"

    class _FakeSticker:
        id = 222
        name = "スタンプ"
        format = _FakeStickerFormat()

    class _FakeAttachment:
        id = 333
        filename = "image.png"
        url = "https://cdn.example.invalid/image.png"
        proxy_url = "https://media.example.invalid/image.png"
        content_type = "image/png"
        size = 1234
        width = 100
        height = 100
        ephemeral = False

    from datetime import datetime, timezone

    class _FakeMessage:
        embeds = [_FakeEmbed()]
        reactions = [_FakeReaction()]
        mentions = [_FakeUser()]
        raw_role_mentions = [444]
        mention_everyone = True
        pinned = True
        tts = False
        type = _FakeType()
        flags = _FakeFlags()
        webhook_id = None
        stickers = [_FakeSticker()]
        edited_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        attachments = [_FakeAttachment()]

    payload = collector.build_raw_payload(_FakeMessage())

    check(payload.get("embeds") == [{"title": "テスト埋め込み"}], "build_raw_payload: embeds を含む")
    check(payload.get("reactions") == [{"emoji": "👍", "count": 3}], "build_raw_payload: reactions を含む")
    check(payload.get("mentions") == ["111"], "build_raw_payload: mentions を含む")
    check(payload.get("mention_roles") == ["444"], "build_raw_payload: mention_roles を含む")
    check(payload.get("mention_everyone") is True, "build_raw_payload: mention_everyone を含む")
    check(payload.get("pinned") is True, "build_raw_payload: pinned を含む")
    check("tts" not in payload, "build_raw_payload: False の tts は含めない (水増ししない)")
    check(payload.get("type") == "default", "build_raw_payload: type を含む")
    check(payload.get("sticker_items")[0]["name"] == "スタンプ", "build_raw_payload: sticker_items を含む")
    check(payload.get("edited_at") == "2026-08-01T00:00:00+00:00", "build_raw_payload: edited_at を含む")
    check(payload.get("attachments")[0]["filename"] == "image.png", "build_raw_payload: attachments の完全な dict を含む")

    # credential / token 様の値が混入していないこと。
    dumped = json.dumps(payload, ensure_ascii=False).lower()
    check("token" not in dumped, "build_raw_payload: token という語が含まれない")
    check("secret" not in dumped, "build_raw_payload: secret という語が含まれない")


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
        test_search_hybrid_short_query(os.path.join(tmpdir, "t6.db"))
        test_search_hybrid_long_query(os.path.join(tmpdir, "t7.db"))
        test_search_like_wildcard_escape(os.path.join(tmpdir, "t8.db"))
        test_search_like_excludes_deleted(os.path.join(tmpdir, "t9.db"))
        test_compute_input_hash(os.path.join(tmpdir, "t10.db"))
        test_summary_cache(os.path.join(tmpdir, "t11.db"))
        test_topics_silver_gold_and_adjudication(os.path.join(tmpdir, "t12.db"))
        test_export_v2_structure(
            os.path.join(tmpdir, "t5.db"),
            os.path.join(tmpdir, "chronica-v2-data.js"),
        )
        test_query_channels_and_stats(os.path.join(tmpdir, "t13.db"))
        test_query_window(os.path.join(tmpdir, "t14.db"))
        test_query_search_with_filters(os.path.join(tmpdir, "t15.db"))
        test_query_resolve_channel_by_name(os.path.join(tmpdir, "t16.db"))
        test_build_raw_payload()
    finally:
        gc.collect()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n=== {PASS_COUNT} assertions PASSED ===")


if __name__ == "__main__":
    main()
