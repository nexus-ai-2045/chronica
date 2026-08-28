# Chronica Bot — Discord リアルタイム収集系

対象サーバーのメッセージを SQLite にリアルタイム蓄積し、既存ビューア
(`viewer/chronica.html`) 用の `chronica-data.js` をいつでも
再生成できるようにする Discord Bot 一式。

private local only。外部送信・deploy・push は禁止 (詳細は `../README.md`)。

## 構成

| file | 役割 |
|---|---|
| `schema.sql` | SQLite スキーマ (messages / channels / sync_state / messages_fts) |
| `store.py` | DB 層。discord.py に依存しない (テスト対象) |
| `envutil.py` | `.env` の手動パーサ (python-dotenv 不使用) |
| `collector.py` | Bot 本体。on_message / edit / delete をリアルタイムに store へ反映 |
| `backfill.py` | 履歴一括取得 CLI。再開可能・`--incremental` で差分取得 |
| `export_v2.py` | SQLite → `chronica-data.js` 生成 (discord.py 非依存) |
| `test_store.py` | store.py / export_v2.py のユニットテスト (discord.py 不要) |

## セットアップ手順

### (1) Discord Bot を作成する (人間の作業)

1. https://discord.com/developers/applications で New Application
2. 左メニュー Bot → Add Bot
3. **Message Content Intent をトグル ON** にする
   (2026-06 時点の制度: 1万ユーザー未満の Bot は審査不要でこのトグルだけで有効化できる)
4. Bot タブの「Reset Token」で token を取得し、`.env` の `DISCORD_BOT_TOKEN` に貼る
   (token はチャット・repo・issue に絶対に貼らない)

### (2) サーバーへの招待 (サーバー管理者の作業が必要)

Bot を対象サーバーに参加させるには、**サーバー管理者による招待が必要**。
以下の最小権限 (View Channels + Read Message History) の invite URL を組み立てて
管理者に送る:

```
https://discord.com/api/oauth2/authorize?client_id=<CLIENT_ID>&scope=bot&permissions=66560
```

- `<CLIENT_ID>` は Developer Portal の General Information タブにある Application ID
- `permissions=66560` は `View Channels (1024) + Read Message History (65536)` の合計値
  (書き込み・管理系権限は含めない)

### (3) `.env` を作る

```
cp .env.example .env
```

`.env` を開いて `DISCORD_BOT_TOKEN` と `GUILD_ALLOWLIST` (対象サーバーの guild ID) を埋める。
`CHRONICA_DB` / `EXPORT_OUT` は既定値のままで良い (`../data/` 配下、gitignore 済み)。

### (4) 依存関係のインストール

```
pip install -r requirements.txt
```

### (5) 運用手順

1. **バックフィル (初回・全期間)**:
   ```
   python backfill.py
   ```
   途中で止まっても再実行すれば `sync_state` を見て続きから (未完了チャンネルのみ) 再開する。
   日次などで差分だけ取りたい場合は:
   ```
   python backfill.py --incremental
   ```

2. **collector を常駐させる** (リアルタイム収集):
   ```
   python collector.py
   ```
   Ctrl+C で終了。以後は on_message / on_raw_message_edit / on_raw_message_delete /
   on_raw_bulk_message_delete が SQLite に反映され続ける。

3. **ビューア用データを生成する** (いつでも何度でも実行可):
   ```
   python export_v2.py
   ```
   `EXPORT_OUT` (既定 `../data/chronica-data.js`) に書き出す。
   これを `viewer/chronica-data.js` にコピーして `chronica.html` を
   file:// で開けば見られる。

4. **コマンドラインから引く** (ビューアを使わず LLM に渡したい場合):
   ```
   python query.py --db ../data/chronica.db search 開発
   python query.py --db ../data/chronica.db window --channel 雑談 --since 2026-08-01
   python query.py --db ../data/chronica.db channels
   python query.py --db ../data/chronica.db stats
   ```
   `--json` で LLM にそのまま渡せる形式になる。
   `--db` は**サブコマンドより前**に置く (親パーサの引数のため)。
   `.env` に `CHRONICA_DB` を書いておけば省略できる。

   `window` は「このチャンネルのこの期間」を時系列で返すので、要約の入力取得に使う。
   検索は 3 文字以上なら FTS5、2 文字以下は LIKE 全表走査に自動で切り替わる
   (日本語の 2 文字語が FTS5 trigram にヒットしないため。`docs/adr/0001-...` 参照)。

## データの扱い

- `data/` 配下 (DB・生成 JS) はすべて `.gitignore` 済み。commit されない。
- `.env` も `.gitignore` 済み。commit されない。
- 削除されたメッセージは物理削除せず `deleted_at` を立てるだけ (`export_v2.py` は除外して出力)。
- 編集されたメッセージは `content` を上書きし `edited_at` を記録する (履歴は保持しない)。
- 専用列に無い受信情報 (embeds / reactions / mentions / pinned など) を `raw_json` 列に保存しているので、
  後から仕様変更があっても再解釈できる。専用列と重複する項目 (id / content / author_id 等) は入れない。
