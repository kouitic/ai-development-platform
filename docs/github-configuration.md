# GitHub設定ガイド

> **適用範囲: パターン②**
> 本書は、`ai-development-platform`が対象システムへ要求するGitHub設定です。プラットフォーム自体の保守リポジトリに対するCodexの作業規約ではありません。両者の境界は`repository-governance.md`を参照してください。

## ブランチ

GitHub Flowを使い、`main`と`ai/issue-<number>-<description>`を使用します。`develop`はMVPで必須ではありません。

## main保護

Repository settingsで、Pull Request必須、required status checks、conversation resolution、CODEOWNERS review、force push禁止、削除禁止を設定します。管理者を含むバイパス方針は人間が決定します。

正式なrequired Checkは`ai-quality/system-review`、`ai-quality/business-review`、`ai-quality/qa-assessment`、`ai-quality/final`です。これらは`.github/workflows/ai-quality-gates.yml`だけが更新します。旧分割review Workflowを併用しません。

## Security

利用可能なプラン/組織設定に応じてSecret scanningとPush protectionを有効にします。検出時は値をIssueや通知へ貼らず、失効と再発行を行います。Actionsは固定アクセスキーではなくOIDCと短期IAM Roleを優先します。

## Actions権限

標準は`contents: read`です。手動の開発WorkflowだけIssueブランチのcommit/pushに`contents: write`を付与し、Issueはread、PRはwriteに限定します。正式レビューは`contents: read`、`issues: read`、`pull-requests: write`、`checks: write`とします。GitHub Environment、本番Secret、本番IAMはどちらにも渡しません。

外部Actionはversion tagではなく、`github-actions-pinning.md`でレビューしたNode.js 24対応commit SHAへ固定します。rootと生成テンプレートは同じSHAを使用し、Action更新だけを独立して検証します。

## ラベル

`.ai-dev`の状態と対応する`ai:*`、`risk:*`、`type:*`、`impact:*`ラベルを作成します。`ai:approved`は手動開発Workflowの必須条件ですが、単独では承認にならず、要件と環境構成のダイジェスト一致も必要です。

## 手動開発Workflow

`.ai-dev/project.yaml`で`github.enabled: true`、`github.gateway: gh`、`github.allowed_actors`を明示します。`allowed_actors`を空にしたまま実Claude開発を起動することはできません。`ANTHROPIC_API_KEY`はRepository Secretへ登録し、Actions画面からmainの`AI開発オーケストレーター`へ承認済みIssue番号を入力します。Workflowのbranch選択をmain以外にした場合は拒否されます。

## 手動品質Workflow

通常CI成功後、Actions画面からmainの`AI quality gates`へ承認済みIssue番号と、そのIssueを`Closes #<number>`で関連付けたopen PR番号を入力します。許可actor、main上のWorkflow、同一repository・非fork PR、default branch、許可head branch、base/head SHAを有料処理前に検証します。PRの作成・更新だけではClaudeを起動せず、通常実行では有料のProvider事前診断も行いません。同じPRの品質Workflowが重複した場合は古い実行を取り消します。

## CODEOWNERS

生成直後の`@replace-with-github-owner`を実在する利用者またはTeamへ変更してから保護を有効にします。`.github/`、`.ai-dev/`、業務ルール、レビュー基準、品質基準、期待結果には必須Ownerを設定します。

## Private利用とライセンス

PrivateでもSecretを保存してよいことにはなりません。外部公開前にライセンス、依存ライセンス、履歴内の機密情報、Issue/PR内容を再監査します。
