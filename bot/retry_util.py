#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord API レート制限 (429) / サーバー側一時障害 (5xx) 向けリトライ判定の純粋関数群。

discord.py に依存しない (import しない) ため、discord.py 未インストールの環境
(CI のクリーン環境等) でもテストできる。async のリトライ実行本体 (asyncio.sleep
を伴う部分) は backfill.py 側に置き、ここでは「リトライすべきか」「何秒待つか」
の判定ロジックだけを切り出す。
"""

from __future__ import annotations

# 最大リトライ回数 (これを超えたら諦める)。
MAX_RETRIES = 5

# 指数バックオフの初期値 (秒)。
INITIAL_BACKOFF_SEC = 1.0

# 指数バックオフの上限 (秒)。Retry-After が長すぎる場合もここで頭打ちにする。
MAX_BACKOFF_SEC = 60.0


def should_retry(status: int) -> bool:
    """HTTP ステータスがリトライ対象 (429 レート制限 / 5xx サーバー側一時障害) か判定する。

    4xx (403/404 等の権限不足・不正リクエスト) はリトライしても解消しないため対象外。
    """
    return status == 429 or 500 <= status < 600


def compute_backoff(attempt: int, retry_after: float | None = None) -> float:
    """待機秒数を計算する。

    Discord の Retry-After ヘッダが読めた場合はその値を尊重する (ただし
    MAX_BACKOFF_SEC で頭打ち)。読めない場合は 1回目=1秒, 2回目=2秒, 3回目=4秒 ...
    と倍々に伸びる指数バックオフにフォールバックする。

    attempt は 1 始まり (1回目のリトライ待機 = attempt=1)。
    """
    if retry_after is not None and retry_after >= 0:
        return min(retry_after, MAX_BACKOFF_SEC)
    backoff = INITIAL_BACKOFF_SEC * (2 ** (attempt - 1))
    return min(backoff, MAX_BACKOFF_SEC)
