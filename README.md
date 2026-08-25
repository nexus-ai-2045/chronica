# Chronica — Discord サーバー年代記ビューア

Discord サーバーの会話を「**チャンネルごとの盛り上がりの流れ**」と「**発言者の動き**」として
振り返るためのローカルツールです。

上段の Streamgraph で数か月分を俯瞰し、気になったチャンネルをクリックすると
発言者レーンにズームして、その期間に誰がいつ何を言ったかを読み直せます。
実測で **27,797 メッセージ / 254 チャンネル / 261 名 / 7 か月分**を単一 HTML で扱えています。

**データはローカルにのみ保存し、このリポジトリはコードと文書だけを管理します。**
境界は [PROCESS_BOUNDARY.md](PROCESS_BOUNDARY.md)、扱ってはいけないデータは
[SECURITY.md](SECURITY.md) を参照してください。

## Discord Context Bridge (DCB) との関係

[nexus-ai-2045/discord-context-bridge](https://github.com/nexus-ai-2045/discord-context-bridge)
（DCB）とは目的が異なる別ツールですが、**設計と安全境界の考え方は DCB を土台にしています**。

| | DCB | Chronica |
|---|---|---|
| 目的 | 送信**前**に文脈を把握する | 会話の**後**を振り返る |
| 時間の向き | これから書く | 過去を読む |
| 取得方式 | ブラウザの可視テキスト（read-only） | Discord Bot API（read-only） |
| 保存 | append-only の local event snippet | ローカル SQLite（FTS5 全文検索つき） |

DCB から引き継いでいるもの:

- **`SECURITY.md` の禁止データ一覧**（実 guild ID・channel ID・handle・private message 本文・
  local absolute path を commit しない）。本リポジトリはこの一覧をそのまま採用しています。
- **`PROCESS_BOUNDARY.md` で「やらないこと」を先に固定する**書き方。
- **local-first / read-only を既定とし、送信系を持たない**という設計方針。

初期のデータは DCB 系のブラウザキャプチャから取り込みました
（`pipeline/build_from_capture.py` がその変換器です）。ただし可視テキストには
返信関係が含まれず、メッセージ ID も世代によって欠けるため、
**継続運用は Bot 経由（`bot/`）を正とします**。

## 構成

| dir | 内容 |
|---|---|
| `bot/` | Discord Bot によるリアルタイム収集。Gateway 常駐（新規・編集・削除の追従）＋履歴バックフィル＋ビューア用データ出力。→ [bot/README.md](bot/README.md) |
| `pipeline/` | ブラウザキャプチャ JSON からビューア用データを生成する変換器（初期取り込み用） |
| `viewer/` | 単一ファイルビューア `chronica.html`。同じディレクトリに `chronica-data.js` を置いて `file://` で開く |
| `crypto/` | 合言葉つき単一 HTML の生成（AES-256-GCM + PBKDF2-SHA256 310,000 回） |
| `data/` | ローカル専用（gitignore）。DB・生成データ・暗号化ビルドの置き場 |

## 使い方

```bash
# 1. Bot をセットアップ（詳細は bot/README.md）
cp bot/.env.example bot/.env      # token と GUILD_ALLOWLIST を設定
pip install -r bot/requirements.txt

# 2. 履歴を取り込む
python bot/backfill.py

# 3. 常駐して以後の発言を追従する
python bot/collector.py

# 4. ビューア用データを書き出して開く
python bot/export_v2.py
# data/chronica-data.js が出るので viewer/ に置いて chronica.html を開く
```

Bot の導入には**対象サーバーの管理者の許可**が必要です。
Message Content Intent の有効化も必要になります（一定規模未満なら審査不要）。

## 設計上の判断

- **重複判定は message ID のみ**。本文ハッシュによる重複除去は、同じ文面の再投稿
  （テンプレート告知など）を別発言と区別できず、実測で 10.5% の欠落を出したため廃止しました。
- **削除は物理削除しない**。`deleted_at` を記録し、出力時に除外します。
- **受信ペイロードを `raw_json` に残す**。後から解釈をやり直せるようにするためです。
- **話題の境界は決め打ちしない**。現在はチャンネル単位で扱い、
  自動セグメンテーションを入れる場合も機械の出力を正本にせず、人間の修正を優先する設計とします。

## サイズの目安（実測）

| メッセージ数 | SQLite DB |
|---|---|
| 27,797 | 41 MB |
| 100,000 | 約 148 MB |
| 1,000,000 | 約 1.5 GB |

暗号化した配布用 HTML は 27,797 件で 15 MB です。配布形式には上限があるため、
規模が大きくなる場合は API 経由で必要分だけ返す構成へ移ります。
