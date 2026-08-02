# Agent定義ガイド

## ファイル

Agentは`.ai-dev/agents/<id>.yaml`に定義し、`.ai-dev/project.yaml`の`agents`から参照します。IDとroleは重複できません。

## 主な項目

- `title`、`role`、`expertise`
- `responsibilities`、`forbidden_actions`、`system_prompt`
- `provider`、`model`、`max_turns`
- `available_tools`、`forbidden_tools`
- `readable_paths`、`writable_paths`、`protected_paths`
- `forbidden_commands`
- `internet_access`、`output_schema`
- 業務レビュー用`review_criteria`、`reference_materials`

## 安全原則

プロンプトだけで権限を表現せず、ツール、パス、コマンド、Schema、状態遷移でも強制します。read-only Agentは`writable_paths`を持てません。保護対象と書込範囲の重複は`validate`が拒否します。

Agentへ宣言できるToolは、Runtime実装がある`Read`、`Glob`、`Grep`、`Write`、`Edit`と、役割上必要なread-only Webに限定します。`Test`、Git、GitHub Issue/comment、PR、CheckはAgent Toolではなくホスト側サービスです。検証コマンドは`.ai-dev/policies/verification.yaml`へargv配列として定義し、shell文字列を使用しません。

上位ポリシーの禁止事項をAgent定義で緩和できません。Agent定義、システムプロンプト、品質基準の変更は人間承認対象です。

## 新しいAIの追加

Phase 2のAIは既存ファイルの責務を拡張せず、新しいID、出力Schema、レビュー種別として追加します。Provider契約は`AgentRequest -> AgentResult`を維持します。Workflowへ追加する前に到達性、差戻し、承認バイパス、最大反復を検証するテストを追加します。

## 業務レビューAIの変更

プロジェクト固有の専門性、確認項目、評価基準、参照資料を具体化します。期待結果と業務ルール自体をAIに生成・変更させず、人間が最終承認します。Webはread-onlyで、認証、フォーム送信、download実行、機密送信を許可しません。
