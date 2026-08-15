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

PR用の統合Workflowでは`provider-preflight`が正式な品質ゲートより先に実行されます。`provider-preflight.json`の`stage`と`error_code`だけを確認し、Providerのエラー本文をログやArtifactへ追加しないでください。

- `models_api`失敗: API接続、資格情報に対応するHTTP status、固定した`claude-sonnet-4-6`の利用可能性を確認します。`provider_model_unavailable`は、その資格情報から固定モデルが列挙されなかったことを示します。
- `token_count_api`警告: `provider_api_error_400_workspace_restriction`だけは`WARN`となり、全4段階が完走すれば`PASS_WITH_WARNINGS`になります。この警告はToken Counting API固有の制約を示し、Messages APIとAgent SDKの成否を後続段階で別に確認します。
- `token_count_api`失敗: Workspace警告以外は停止します。モデル参照は成功しているため、同じ固定モデルとuser messageの入力形式、HTTP status、利用権限を確認します。
- `messages_api`失敗: 資格情報とモデル参照は成功しています。Token Countingが`PASS`または許可済み`WARN`であることを確認し、`provider_api_error_400_billing_credit_balance_low`ならClaude ConsoleのAPI credit残高、`provider_api_error_400_max_tokens_invalid`なら出力上限、その他の固定コードなら組織・Workspace・地域・モデル制約を確認します。
- `agent_sdk`失敗: 直接Messages APIは成功しています。Agent SDKまたは同梱Claude CLIの要求組み立て、認証経路、実行環境を確認します。
- 4段階成功後に正式品質ゲートが失敗: Structured Outputs、ツール・sandbox設定、正式Prompt、task context、Agent固有設定との差を確認します。

`models_api`と`token_count_api`は生成を行いません。有料生成は`messages_api`と`agent_sdk`の最大2回で、前者は16 output token以下、後者は1 turnかつ0.05 USD以下です。再実行前に、対象commit SHAと既存Artifactを確認してください。
