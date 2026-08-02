# GitHub設定ガイド

## ブランチ

GitHub Flowを使い、`main`と`ai/issue-<number>-<description>`を使用します。`develop`はMVPで必須ではありません。

## main保護

Repository settingsで、Pull Request必須、required status checks、conversation resolution、CODEOWNERS review、force push禁止、削除禁止を設定します。管理者を含むバイパス方針は人間が決定します。

正式なrequired Checkは`ai-quality/system-review`、`ai-quality/business-review`、`ai-quality/qa-assessment`、`ai-quality/final`です。これらは`.github/workflows/ai-quality-gates.yml`だけが更新します。旧分割review Workflowを併用しません。

## Security

利用可能なプラン/組織設定に応じてSecret scanningとPush protectionを有効にします。検出時は値をIssueや通知へ貼らず、失効と再発行を行います。Actionsは固定アクセスキーではなくOIDCと短期IAM Roleを優先します。

## Actions権限

標準は`contents: read`です。Issue同期ジョブだけ`issues: write`、正式レビュー投稿だけ`pull-requests: write`を付与します。`contents: write`、Environment Secret、本番IAMは通常レビューへ渡しません。

## ラベル

`.ai-dev`の状態と対応する`ai:*`、`risk:*`、`type:*`、`impact:*`ラベルを作成します。ラベルは表示補助であり、SQLite状態や承認Guardを上書きしません。

## CODEOWNERS

生成直後の`@replace-with-github-owner`を実在する利用者またはTeamへ変更してから保護を有効にします。`.github/`、`.ai-dev/`、業務ルール、レビュー基準、品質基準、期待結果には必須Ownerを設定します。

## Private利用とライセンス

PrivateでもSecretを保存してよいことにはなりません。外部公開前にライセンス、依存ライセンス、履歴内の機密情報、Issue/PR内容を再監査します。
