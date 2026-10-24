#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""エクスポータとビューアの contract test。

`export_v2.py` が書き出すファイルを、`viewer/chronica.html` が実際に読めるかを検証する。
両者は「ファイル名」と「グローバル変数名」の 2 点でしか繋がっておらず、どちらも
文字列リテラルのため、片方だけ変えても単体テストも CI も落ちない。

v0.1.0 はこの不一致 (エクスポータが chronica-v2-data.js を書き、viewer は
chronica-data.js を読む) のまま出荷され、README どおりに操作すると空画面になった。
そのとき単体テストと CI は全て緑だった。

consumer-driven contract test の考え方に沿い、consumer (viewer) が要求する形を
provider (exporter) が満たしているかを検査する。discord.py には依存しない。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
REPO = BOT_DIR.parent
VIEWER = REPO / "viewer" / "chronica.html"
FIXTURE_CONTENT = "contract test fixture"

sys.path.insert(0, str(BOT_DIR))

import envutil  # noqa: E402
import export_v2  # noqa: E402
import store  # noqa: E402
from test_store import make_message  # noqa: E402

_passed = 0
_failed: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed
    suffix = (" — " + detail) if detail else ""
    if cond:
        _passed += 1
        print("PASS: " + label)
    else:
        _failed.append(label + suffix)
        print("FAIL: " + label + suffix)


def viewer_expectations() -> tuple[str, str]:
    """viewer が要求する (データファイル名, グローバル変数名) を HTML から読む。"""
    html = VIEWER.read_text(encoding="utf-8")

    m = re.search(r'<script\s+src="\./([^"]+\.js)"', html)
    if not m:
        raise AssertionError("viewer/chronica.html に data 用の script src が見つからない")

    g = re.search(r"window\.([A-Z][A-Z0-9_]+)", html)
    if not g:
        raise AssertionError("viewer/chronica.html に window の global 参照が見つからない")

    return m.group(1), g.group(1)


def build_fixture_db(path: Path) -> None:
    """最小の fixture DB を作る。MessageRecord の組み立ては test_store の make_message を再利用する。"""
    conn = store.get_connection(str(path))
    store.init_db(conn)
    store.upsert_message(conn, make_message("m1", content=FIXTURE_CONTENT))
    conn.commit()
    conn.close()


def main() -> int:
    want_file, want_global = viewer_expectations()
    print("viewer が要求する形: file=" + want_file + " global=window." + want_global)
    print("")

    # WAL の -wal / -shm が残ると Windows で削除に失敗するため、後片付けは自前で行う
    td = tempfile.mkdtemp(prefix="chronica-contract-")
    try:
        tmp = Path(td)
        db = tmp / "chronica.db"
        build_fixture_db(db)

        # row_factory が要るので store 経由で開く (素の sqlite3.connect では dict(row) が落ちる)
        conn = store.get_connection(str(db))
        out = tmp / want_file
        export_v2.export_to_js(conn, out, server_name="contract-test")
        conn.close()

        check("エクスポータが viewer の要求するファイル名で書ける", out.exists())

        text = out.read_text(encoding="utf-8")
        m = re.match(r"\s*window\.([A-Z][A-Z0-9_]+)\s*=\s*", text)
        check("出力が window.GLOBAL = ... 形式である", m is not None)

        got_global = m.group(1) if m else "(なし)"
        check(
            "グローバル変数名が viewer の参照と一致する",
            got_global == want_global,
            "exporter=" + got_global + " / viewer=" + want_global,
        )

        body = text[text.index("=") + 1:].rstrip().rstrip(";")
        try:
            payload = json.loads(body)
            ok_json = True
        except Exception as exc:  # noqa: BLE001
            payload, ok_json = None, False
            print("       JSON parse error: " + str(exc))

        check("出力本体が JSON として解釈できる", ok_json)
        check(
            "投入したメッセージが payload に含まれる",
            bool(payload) and FIXTURE_CONTENT in text,
            "payload に fixture 本文が無い (エクスポートが空)",
        )
    finally:
        shutil.rmtree(td, ignore_errors=True)

    default_out = Path(envutil.get_export_out_path()).name
    check(
        "EXPORT_OUT の既定値の basename が viewer の参照と一致する",
        default_out == want_file,
        "既定=" + default_out + " / viewer=" + want_file,
    )

    print("")
    if _failed:
        print("=== " + str(len(_failed)) + " FAILED / " + str(_passed) + " passed ===")
        for f in _failed:
            print("  - " + f)
        return 1

    print("=== " + str(_passed) + " checks PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
