# Contributing

Chronica への貢献を歓迎します。まず [PROCESS_BOUNDARY.md](PROCESS_BOUNDARY.md) を読み、
このツールが**やらないこと**を確認してください。

## 最重要のルール

**実データを含む変更は受け付けません。**

- 実在する guild ID / channel ID / user ID / handle
- private message の本文
- 個人環境の絶対パス
- token / webhook URL

`.gitignore` は生成物を遮断しますが、**コード本文への埋め込みは防げません**。
定数・サンプル・テストデータ・フォールバックに実データを書かないでください。
詳細は [SECURITY.md](SECURITY.md) を参照。

## 開発の流れ

1. Issue で目的を共有する（小さな修正は不要）
2. ブランチを切る
3. 変更する
4. テストを通す
5. PR を出す

## テスト

```bash
python bot/test_store.py
```

Discord への接続なしで動きます（`store.py` / `export_v2.py` は `discord.py` に依存しません）。
DB 層と出力層を変更したらこのテストを通してください。

## コードの方針

- コメントとドキュメントは日本語、識別子は英語
- 標準ライブラリを優先し、依存を増やさない
- 収集・保存・出力の層を混ぜない
- 推定値を事実として保存しない（由来を残す）

## PR に書くこと

- 何を変えたか
- なぜ必要か
- どう検証したか（実行したコマンドと結果）
- 実データを含まないことの確認
