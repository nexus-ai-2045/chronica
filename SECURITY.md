# Security Policy

Chronica は local-first です。収集した Discord データはローカルにのみ保存し、
このリポジトリはコードと文書だけを管理します。

## Supported Boundary

- 収集は利用者自身が権限を持つサーバーに対してのみ行います。
- 保存先はローカルの SQLite / JSON です（`data/` 配下・gitignore 済み）。
- Bot token は `.env` から読み、リポジトリには置きません。
- Chronica は Discord へメッセージを送信しません（読み取りのみ）。
- 収集対象サーバーは `GUILD_ALLOWLIST` で明示指定したものに限ります。

## Sensitive Data

次のものを commit しません（DCB の同名ポリシーに準拠）。

- Discord user token、bot token、webhook URL。
- 実 guild ID、channel ID、user ID、handle。
- private message 本文、および実会話を含む生成物
  （`chronica-data.js` / `*-locked.html` / `*.db` / `*.ndjson`）。
- local absolute path。
- Browser cookie、profile、session store、screenshot。

`.gitignore` で機械的に遮断していますが、**コード本文への埋め込みは
gitignore では防げません**。定数・サンプル・フォールバックデータに
実データを書かないでください。

## Reporting

release candidate に sensitive data が見つかった場合は公開を止めます。
必要なら credential を rotate し、該当データを取り除いてから再検査します。
