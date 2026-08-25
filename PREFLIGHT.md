# Preflight

<!-- repo-preflight:review-record -->

repo-preflight の検査記録。公開・共有・リリースの前に確認する項目です。


## 毎回確認する

- [ ] 実データが含まれていない
      （guild ID / channel ID / handle / message 本文 / 絶対パス / token）
- [ ] `git ls-files` にコードと文書以外が無い
- [ ] `python bot/test_store.py` が通る
- [ ] README の記述が実装と一致している

## 公開・リリース時に追加で確認する

- [ ] LICENSE がある
- [ ] SECURITY.md の禁止データ一覧が最新
- [ ] PROCESS_BOUNDARY.md の「やらないこと」が実装と矛盾しない
- [ ] 履歴（過去 commit）にも実データが無い
- [ ] commit 名義が公開用のもので統一されている

## 検査コマンド

```bash
# 追跡ファイルの一覧
git ls-files

# 履歴を含めた個人パスの検査
git rev-list --objects --all | \
  while read sha path; do
    git cat-file -t "$sha" 2>/dev/null | grep -q blob && \
    git cat-file -p "$sha" | grep -l "Users/" >/dev/null && echo "$path"
  done
```

## 履歴に混入していた場合

**公開を止めます。** 削除して commit し直すだけでは履歴に残ります。
squash してやり直すか、新しいリポジトリへ clean な履歴で移してください。

## 検査記録

### 2026-08-25 — v0.1.0 公開前

| 項目 | 結果 |
|---|---|
| clean_worktree | pass |
| secret_scan | pass |
| personal_path_scan | pass |
| commit_identity | pass |
| origin | pass |
| required_documents | pass |

**経緯**: 当初 private リポジトリで開発していたが、公開前の検査で
ビューアのフォールバックデータに実会話 18 行、パイプラインに実サーバー ID、
正規化マップに実在ハンドル名が含まれていることが判明した。

対応として (1) 実データを含む旧ビューア・旧パイプラインを削除、
(2) サーバー ID を環境変数化、(3) 正規化マップを空に、(4) 固有名詞を一般化した。
さらに**削除しても履歴に残る**ため、単一 commit へ squash した clean な履歴で
新しいリポジトリを作り直した。旧リポジトリは private のまま archive として保持している。

**教訓**: `.gitignore` は生成物を遮断するが、**コード本文への実データ埋め込みは防げない**。
フォールバック・サンプル・定数に実データを書かないこと。
