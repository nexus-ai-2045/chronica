# Preflight

公開・共有・リリースの前に確認する項目です。

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
