# Chronica

**Discord サーバーの会話を、話題の流れと発言者の動きとして振り返るローカルツール。**

チャンネルごとの盛り上がりを数か月分ひと目で見て、気になった時期にズームして
「誰がいつ何を言ったか」を読み直せます。

実測: **27,797 メッセージ / 254 チャンネル / 261 名 / 7 か月分**を単一 HTML で表示。

---

## できること

- **チャンネルの川** — 期間全体の盛り上がりを Streamgraph で俯瞰
- **発言者タイムライン** — チャンネルを選ぶと、誰がいつ発言したかのレーン表示にズーム
- **全文検索** — 本文と発言者名の部分一致（日本語対応）
- **リアルタイム更新** — Bot 常駐で新規・編集・削除を自動追従
- **暗号化して共有** — 合言葉つきの単一 HTML を書き出せる

## データの扱い

- 収集したデータは**ローカルにのみ保存**します（SQLite）。
- **このリポジトリにはコードと文書しか入りません。** 実データ・DB・生成物・token は
  `.gitignore` で遮断しています。
- Chronica は Discord へ**書き込みません**（読み取り専用）。

詳しくは [PROCESS_BOUNDARY.md](PROCESS_BOUNDARY.md)（やること・やらないこと）と
[SECURITY.md](SECURITY.md)（扱ってはいけないデータ）を読んでください。

## 使い方

### 1. Bot を用意する

Discord Developer Portal で Bot を作り、**Message Content Intent** を有効にします。
対象サーバーへの導入には**サーバー管理者の許可**が必要です。
必要な権限は View Channels と Read Message History だけです。

```bash
cp bot/.env.example bot/.env    # token と GUILD_ALLOWLIST を書く
pip install -r bot/requirements.txt
```

### 2. 履歴を取り込む

```bash
python bot/backfill.py              # 全履歴
python bot/backfill.py --incremental  # 差分だけ
```

### 3. 常駐して追従する

```bash
python bot/collector.py
```

### 4. 見る

```bash
python bot/export_v2.py             # data/chronica-data.js を生成
```

生成された `chronica-data.js` を `viewer/` に置き、`viewer/chronica.html` を
ブラウザで開きます（サーバー不要）。

### 5. 共有する（任意）

```bash
python crypto/build_chronica_locked.py <合言葉>
```

合言葉つきの単一 HTML ができます。**復号後のデータは回収できない**ので、
渡す相手を決めてから使ってください。

## 構成

| ディレクトリ | 役割 |
|---|---|
| `bot/` | Bot による収集（常駐・バックフィル・データ出力）→ [詳細](bot/README.md) |
| `viewer/` | 単一ファイルビューア |
| `crypto/` | 合言葉つき HTML の生成（AES-256-GCM / PBKDF2-SHA256 310,000 回） |
| `pipeline/` | ブラウザキャプチャからの初期取り込み（移行用） |
| `data/` | ローカル専用（gitignore）。DB と生成物の置き場 |

## 設計上の判断

**重複判定は message ID のみ。** 本文ハッシュでの重複除去は、同じ文面の再投稿
（テンプレート告知など）を別発言と区別できません。実測で 10.5% の欠落を出したため廃止しました。

**削除は物理削除しない。** `deleted_at` を記録し、出力時に除外します。
Discord 側の削除に追従しつつ、いつ消えたかを失いません。

**受信ペイロードを残す。** `raw_json` に原本を保存し、後から解釈をやり直せるようにしています。

**話題の境界を決め打ちしない。** 現在はチャンネル単位で扱います。自動分割を入れる場合も
機械の出力を正本にせず、人間の修正を優先する設計とします。

## サイズの目安（実測）

| メッセージ数 | SQLite DB |
|---|---|
| 27,797 | 41 MB |
| 100,000 | 約 148 MB |
| 1,000,000 | 約 1.5 GB |

暗号化した配布用 HTML は 27,797 件で 15 MB です。配布形式には上限があるため、
規模が大きくなる場合は API 経由で必要分だけ返す構成へ移ります。

## Discord Context Bridge (DCB) との関係

[discord-context-bridge](https://github.com/nexus-ai-2045/discord-context-bridge) とは
目的が異なる別ツールですが、**設計と安全境界の考え方は DCB を土台にしています**。

| | DCB | Chronica |
|---|---|---|
| 目的 | 送信**前**に文脈を把握する | 会話の**後**を振り返る |
| 時間の向き | これから書く | 過去を読む |
| 取得 | ブラウザの可視テキスト | Discord Bot API |
| 保存 | append-only の local snippet | ローカル SQLite（全文検索つき） |

DCB から引き継いだもの:

- `SECURITY.md` の**禁止データ一覧**（実 guild ID・handle・private message 本文・
  絶対パスを commit しない）
- `PROCESS_BOUNDARY.md` で**「やらないこと」を先に固定する**書き方
- **local-first / read-only を既定とし、送信系を持たない**方針

初期データは DCB 系のブラウザキャプチャから取り込みました（`pipeline/` がその変換器）。
ただし可視テキストには返信関係が含まれないため、**継続運用は Bot 経由を正とします**。

## ライセンス

[MIT](LICENSE)
