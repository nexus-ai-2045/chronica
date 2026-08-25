-- Chronica bot 用 SQLite スキーマ。
--
-- 設計方針 (壁打ち採択事項):
--   - dedupe は message_id (PK) のみ。本文 hash は使わない。
--   - 削除は物理削除せず deleted_at を記録する (エクスポート時に除外)。
--   - 編集は content を更新し edited_at を記録する。
--   - 受信 payload の raw JSON を raw_json に保存し、後で再解釈できるようにする。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- メッセージ本体
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    thread_parent_id TEXT,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    ts TEXT NOT NULL,                  -- メッセージ作成時刻 (ISO8601, UTC)
    edited_at TEXT,                    -- 直近編集時刻 (ISO8601, UTC)。未編集なら NULL
    deleted_at TEXT,                   -- 削除検知時刻 (ISO8601, UTC)。未削除なら NULL
    content TEXT NOT NULL DEFAULT '',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    reply_to_id TEXT,                  -- 返信先 message_id (無ければ NULL)
    raw_json TEXT,                     -- 受信 payload の生 JSON (再解釈用)
    ingested_at TEXT NOT NULL,         -- このレコードを book に取り込んだ時刻 (ISO8601, UTC)
    source TEXT NOT NULL DEFAULT 'gateway'  -- 'gateway' | 'backfill'
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_ts ON messages (channel_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_guild ON messages (guild_id);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages (reply_to_id);

-- チャンネル一覧 (スレッドも含む。thread は parent_id にフォーラム/親チャンネルを持つ)
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,             -- discord.py の ChannelType 文字列表現
    parent_id TEXT,                 -- スレッドの場合の親チャンネル ID
    last_seen_ts TEXT NOT NULL      -- このチャンネル情報を最後に更新した時刻 (ISO8601, UTC)
);

-- バックフィル進捗 (チャンネル単位で再開可能にする)
CREATE TABLE IF NOT EXISTS sync_state (
    channel_id TEXT PRIMARY KEY,
    backfill_done INTEGER NOT NULL DEFAULT 0,  -- 0/1
    oldest_id TEXT,                             -- これまでに取得した最古の message_id
    newest_id TEXT,                             -- これまでに取得した最新の message_id
    updated_at TEXT NOT NULL
);

-- 全文検索 (content のみ)。messages と content_rowid で疎に同期する。
-- tokenize='trigram': 日本語 (CJK) は単語境界が無く既定の unicode61 では
-- 部分一致検索ができないため、3文字以上の部分文字列検索ができる trigram を使う
-- (2文字以下のクエリはヒットしない制約がある)。
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid',
    tokenize='trigram'
);

-- messages の insert/update/delete を messages_fts に反映するトリガ。
-- rowid は sqlite の暗黙列 (INTEGER PRIMARY KEY ではないため messages にも自動付与される)。
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

-- ここから ADR 0002 (要約キャッシュと話題レイヤ) の派生テーブル。
-- messages / channels / sync_state / messages_fts およびそのトリガは一切変更しない。

-- 要約キャッシュ。input_hash + model + prompt_version が同じなら再利用し、
-- LLM を再度呼ばない (cache key derivation)。本文そのものはキーに使わない。
CREATE TABLE IF NOT EXISTS summaries (
    summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_kind TEXT NOT NULL,           -- 'channel_period' | 'message_range'
    channel_id TEXT NOT NULL,
    period_start TEXT,                  -- ISO8601 (UTC)。scope_kind='channel_period' で使用
    period_end TEXT,                    -- ISO8601 (UTC)。scope_kind='channel_period' で使用
    range_start_id TEXT,                -- message_id。scope_kind='message_range' で使用
    range_end_id TEXT,                  -- message_id。scope_kind='message_range' で使用
    input_hash TEXT NOT NULL,           -- 入力メッセージ集合 (message_id 列) から導出したハッシュ
    input_message_count INTEGER NOT NULL,
    summary_text TEXT NOT NULL,
    model TEXT NOT NULL,                -- 使用したモデル識別子
    prompt_version TEXT NOT NULL,       -- 使用したプロンプトのバージョン
    parent_summary_id INTEGER,          -- tree_summarize (要約の要約) の親。無ければ NULL
    created_at TEXT NOT NULL,
    superseded_at TEXT,                 -- 作り直されて無効化された時刻。有効なら NULL
    FOREIGN KEY (parent_summary_id) REFERENCES summaries(summary_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_cache_key
    ON summaries (input_hash, model, prompt_version);
CREATE INDEX IF NOT EXISTS idx_summaries_channel ON summaries (channel_id);
CREATE INDEX IF NOT EXISTS idx_summaries_parent ON summaries (parent_summary_id);

-- 話題 (topic segmentation の結果)。origin で機械出力 (silver) と
-- 人間確定版 (gold) を分ける。silver をそのまま正本にしない。
CREATE TABLE IF NOT EXISTS topics (
    topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    label TEXT NOT NULL,
    origin TEXT NOT NULL,               -- 'silver' | 'gold'
    starts_at TEXT NOT NULL,            -- ISO8601 (UTC)
    ends_at TEXT NOT NULL,              -- ISO8601 (UTC)
    algorithm TEXT,                     -- origin='silver' の場合の生成手法名
    algorithm_version TEXT,
    created_at TEXT NOT NULL,
    superseded_at TEXT                  -- adjudication で置き換えられた時刻。有効なら NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_channel ON topics (channel_id);
CREATE INDEX IF NOT EXISTS idx_topics_starts_at ON topics (starts_at);
CREATE INDEX IF NOT EXISTS idx_topics_origin ON topics (origin);

-- 話題とメッセージの対応 (多対多)。
CREATE TABLE IF NOT EXISTS topic_messages (
    topic_id INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (topic_id, message_id),
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id),
    FOREIGN KEY (message_id) REFERENCES messages(message_id)
);

CREATE INDEX IF NOT EXISTS idx_topic_messages_message ON topic_messages (message_id);

-- 人間による silver -> gold の裁定記録。
CREATE TABLE IF NOT EXISTS adjudications (
    adjudication_id INTEGER PRIMARY KEY AUTOINCREMENT,
    silver_topic_id INTEGER NOT NULL,
    gold_topic_id INTEGER,              -- 'reject' の場合は NULL もありうる
    action TEXT NOT NULL,               -- 'accept' | 'split' | 'merge' | 'relabel' | 'reject'
    note TEXT,
    decided_at TEXT NOT NULL,
    FOREIGN KEY (silver_topic_id) REFERENCES topics(topic_id),
    FOREIGN KEY (gold_topic_id) REFERENCES topics(topic_id)
);

CREATE INDEX IF NOT EXISTS idx_adjudications_silver ON adjudications (silver_topic_id);
CREATE INDEX IF NOT EXISTS idx_adjudications_gold ON adjudications (gold_topic_id);
