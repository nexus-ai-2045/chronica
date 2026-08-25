<div align="center">

# Chronica

**Discord サーバーの会話を、話題の流れと発言者の動きとして振り返る**

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/nexus-ai-2045/chronica?style=flat-square&color=6f42c1)](https://github.com/nexus-ai-2045/chronica/releases)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Local only](https://img.shields.io/badge/data-local%20only-1baf7a?style=flat-square)](SECURITY.md)
[![Read only](https://img.shields.io/badge/Discord-read%20only-5865F2?style=flat-square&logo=discord&logoColor=white)](PROCESS_BOUNDARY.md)

</div>

<br>

<div align="center">
<img src="docs/images/01-overview.png" width="880" alt="チャンネルの川 — 期間全体の盛り上がりを俯瞰">
</div>

<br>

数か月分の会話が、**チャンネルごとの色の帯**になって流れます。<br>
どこが盛り上がっていたか、何が静かになったかが、スクロールせずに見えます。

---

## 🎯 何ができるか

<table>
<tr>
<td width="50%" valign="top">

### 📊 チャンネルの川
期間全体の盛り上がりを俯瞰する。<br>
上位14チャンネル＋その他に集約して表示。

</td>
<td width="50%" valign="top">

### 👥 発言者タイムライン
チャンネルを選ぶと、誰がいつ発言したかへズーム。

</td>
</tr>
<tr>
<td valign="top">

### 🔍 全文検索
本文と発言者名の部分一致。日本語対応。

</td>
<td valign="top">

### 🔄 リアルタイム更新
Bot 常駐で新規・編集・削除を自動追従。

</td>
</tr>
</table>

<br>

### 選ぶと、その人たちの動きが見える

<div align="center">
<img src="docs/images/02-timeline.png" width="880" alt="発言者タイムライン — 誰がいつ発言したか">
</div>

<br>

### 選ぶ前は、全体の呼吸だけを見る

<div align="center">
<img src="docs/images/03-density.png" width="880" alt="未選択時は日次密度のみを表示">
</div>

<br>

### 言葉から探す

<div align="center">
<img src="docs/images/04-search.png" width="880" alt="全文検索 — 本文と発言者名の部分一致">
</div>

---

## 🔒 データの扱い

<table>
<tr><td>🏠</td><td><b>ローカルにのみ保存</b></td><td>収集データは SQLite に入り、外へ出ません</td></tr>
<tr><td>📦</td><td><b>リポジトリはコードだけ</b></td><td>実データ・DB・生成物・token は遮断済み</td></tr>
<tr><td>👀</td><td><b>読み取り専用</b></td><td>Discord へ書き込みません</td></tr>
<tr><td>✅</td><td><b>対象を明示指定</b></td><td>許可リストに書いたサーバーだけを収集</td></tr>
</table>

詳細 → [PROCESS_BOUNDARY.md](PROCESS_BOUNDARY.md)（やること・やらないこと）／ [SECURITY.md](SECURITY.md)（扱ってはいけないデータ）

---

## 🚀 使い始める

| | 手順 | 補足 |
|---|---|---|
| **1** | Discord Developer Portal で Bot を作る | Message Content Intent を有効に |
| **2** | サーバー管理者に導入を依頼する | 権限は View Channels と Read Message History のみ |
| **3** | `.env` に token と対象サーバーを書く | `bot/.env.example` を複製 |
| **4** | 履歴を取り込む | `bot/backfill.py` |
| **5** | 常駐して追従する | `bot/collector.py` |
| **6** | ビューア用データを出す | `bot/export_v2.py` |
| **7** | ブラウザで開く | `viewer/chronica.html`（サーバー不要） |

共有したいときは、合言葉つきの単一 HTML を書き出せます（AES-256-GCM / PBKDF2 310,000 回）。<br>
**復号後のデータは回収できない**ので、渡す相手を決めてから使ってください。

<details>
<summary>📁 ディレクトリ構成</summary>

<br>

| ディレクトリ | 役割 |
|---|---|
| `bot/` | Bot による収集（常駐・バックフィル・データ出力）→ [詳細](bot/README.md) |
| `viewer/` | 単一ファイルビューア |
| `crypto/` | 合言葉つき HTML の生成 |
| `pipeline/` | ブラウザキャプチャからの初期取り込み（移行用） |
| `data/` | ローカル専用（gitignore）。DB と生成物の置き場 |

</details>

---

## 📐 設計上の判断

<table>
<tr>
<td width="34%" valign="top">

**重複判定は ID のみ**

本文ハッシュでの重複除去は、同じ文面の再投稿を別発言と区別できません。実測で **10.5% の欠落**を出したため廃止しました。

</td>
<td width="33%" valign="top">

**削除は物理削除しない**

削除フラグを記録し、出力時に除外します。Discord 側の削除に追従しつつ、いつ消えたかを失いません。

</td>
<td width="33%" valign="top">

**受信内容を残す**

原本を保存し、後から解釈をやり直せるようにしています。

</td>
</tr>
</table>

---

## 📏 サイズの目安（実測）

| メッセージ数 | SQLite DB | |
|---:|---:|---|
| 27,797 | **41 MB** | 7 か月・254 チャンネル・261 名の実績値 |
| 100,000 | 約 148 MB | |
| 1,000,000 | 約 1.5 GB | |

暗号化した配布用 HTML は 27,797 件で 15 MB。配布形式には上限があるため、規模が大きくなる場合は API 経由で必要分だけ返す構成へ移ります。

---

## 🔗 Discord Context Bridge との関係

[discord-context-bridge](https://github.com/nexus-ai-2045/discord-context-bridge) とは目的が異なる別ツールですが、**設計と安全境界の考え方はそちらを土台にしています**。

| | DCB | Chronica |
|---|---|---|
| **目的** | 送信**前**に文脈を把握する | 会話の**後**を振り返る |
| **時間の向き** | これから書く | 過去を読む |
| **取得** | ブラウザの可視テキスト | Discord Bot API |
| **保存** | append-only の local snippet | ローカル SQLite（全文検索つき） |

DCB から引き継いだもの — **禁止データ一覧**（実 ID・handle・本文・絶対パスを commit しない）／ **「やらないこと」を先に固定する**書き方／ **local-first・read-only を既定とし送信系を持たない**方針。

Bot を使わずローカル保存だけしたい場合は、DCB 側が使えます。

---

<div align="center">

**[📖 貢献する](CONTRIBUTING.md)** ・ **[🔐 セキュリティ](SECURITY.md)** ・ **[📋 公開前チェック](PREFLIGHT.md)**

<sub>スクリーンショットはすべて架空のデモデータです。MIT License.</sub>

</div>
