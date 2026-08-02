# トラブルシューティング

## `uv`が見つからない

`uv --version`を確認し、未導入なら公式手順または`python -m pip install uv`を利用します。組織端末では管理者の導入方針を優先します。cache書込権限がない場合は、アクセス可能な専用`UV_CACHE_DIR`を設定します。

## `gh`またはGitHub認証がない

Mock Gatewayなら実行できます。実連携時だけGitHub CLIを導入して`gh auth status`を確認します。token値をログやIssueへ貼り付けないでください。

## `validate`がSecretを検出した

検出値は再表示しません。ファイルをコミットせず、既に共有された可能性があればSecretを失効・再発行します。ダミー値でも実キー形式を避け、`.env.example`は空値または明確なplaceholderにします。

## YAML/Schemaエラー

ファイル名、インデント、必須項目、列挙値を確認します。Agent IDはファイル参照と一致させ、未知ツールやread-onlyの書込範囲を削除します。保護対象を緩和して通過させてはいけません。

## タスクが`BLOCKED`

最大反復、費用上限、QA証拠不足を監査ログで確認します。未解決論点、業務影響、追加作業、費用、推奨案を人間が判断します。反復回数や品質基準を無断で緩和しません。

## 承認が拒否される

Issue、stage、commit SHA、承認者が必要です。対象SHAがタスクの現在SHAと一致するか、状態が`HUMAN_APPROVAL_REQUIRED`か確認します。「進めて」等の曖昧な会話は承認になりません。

## Windowsの一時ディレクトリアクセス

制限環境ではpytestの一時領域をリポジトリ内の`.pytest-tmp`へ設定します。本リポジトリの`pyproject.toml`では既に設定済みです。

## Claude/GitHub APIエラー

外部詳細や資格情報を表示せず、安全停止します。Mockでローカル再現し、ネットワーク、認証、有効期限、SDK/CLI互換を別々に確認します。mainや本番への迂回操作は行いません。
