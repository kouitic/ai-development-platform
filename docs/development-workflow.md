# この基盤を使った開発ワークフロー

> **適用範囲: パターン②**
> 本書は、`ai-development-platform`を利用して対象システムを開発する際の規約です。プラットフォーム自体を人間とCodexが保守する手順には適用しません。両者の境界は`repository-governance.md`を参照してください。

## 1. 新規リポジトリ作成

Privateリポジトリを作成し、mainを初期ブランチにします。Secretや実データを初期コミットへ含めません。

## 2. 初期化

`ai-dev init <project-name>`を実行します。競合時は自動上書きされないため、差分を人間が判断します。

## 3. 診断

`ai-dev doctor`でPython、uv、Git、gh、認証、外部GitHub制御を確認し、`ai-dev validate`で設定・安全ポリシーを確認します。

## 4. プロジェクト設定

interaction、予算、反復、GitHub Gateway、保護パスを設定します。下位設定で標準禁止事項を緩和できません。

## 5. 業務レビューAI

`.ai-dev/agents/business-reviewer.yaml`に専門家としての肩書、専門領域、責務、確認項目、評価基準、参照資料を設定し、人間が承認します。

## 6. 業務要件整理

`ai-dev chat`で目的、背景、対象、対象外、業務要件、受入条件、期待結果を整理します。技術選択ではなく、業務、利用者、費用、運用、停止、データ、リスクを決定します。

正式IssueにはIssue Formの構造化YAMLで要件ID、種別、説明、受入条件、必須性を記録します。自然言語からAIが生成した候補は`REQUIREMENTS_APPROVAL_REQUIRED`で停止し、`approve --stage requirements`によるcommit-boundな人間承認後だけ後続工程で使用します。

## 7. デプロイ先と環境

利用場所、利用者数、利用時間、停止許容、復旧・データ損失、検証環境、本番相当データ、費用、運用担当、外部接続、将来増加を確認します。サービス名の選択は専門AIが条件から提案します。

## 8. GitHub設定

branch protection、CODEOWNERS、Secret scanning、Push protection、Actions最小権限を設定します。詳細は`github-configuration.md`を参照します。

## 9. Secret設定

ローカルはOS環境変数またはGit対象外`.env`、ActionsはRepository/Environment Secrets、クラウドは専用Secret管理とOIDCを使用します。

## 10. Issue作成

`ai-dev chat "依頼" --create-issue`またはIssue formで正式タスクを作成します。未確定会話はIssueへ投稿しません。

## 11. AI作業開始

`ai-dev run --issue <番号> --commit-sha <SHA>`を実行します。MVPでは変更タスクは同時に1件です。

## 12–14. 設計・実装・テスト

設計開発AIが要件を仕様化し、設計、実装、単体・結合相当の自動テスト、要件追跡を更新します。mainへのpushや本番操作は行いません。

## 15. システムレビュー

読み取り専用AIが設計整合、コード、セキュリティ、性能、可用性、保守性、テスト、Secret、環境分離を評価します。不合格時は修正受入条件を伴って差戻します。

## 16. 業務レビュー

業務レビューAIが承認済み業務要件、業務ルール、期待結果、根拠との整合を評価します。外部情報は未信頼データとして扱います。

## 17. QA評価

QAはテスト、両レビュー、要件追跡、バグ、残存リスクを証拠として`PASS`、`PASS_WITH_CONDITIONS`、`REJECT`、`INSUFFICIENT_EVIDENCE`を判定します。証拠不足はPASSになりません。

## 18. 修正反復

不合格時は最大3回まで`REWORK_REQUIRED`から再実装します。収束しない場合は`BLOCKED`となり、未解決事項と人間判断を提示します。

## 19. 人間承認

`HUMAN_APPROVAL_REQUIRED`で停止します。人間はIssue、stage、commit SHA、証拠、残存リスクを確認し、`approve`または`reject`を明示実行します。

## 20. マージ

AIはmainを自動マージしません。GitHub側の必須レビュー、Actions、CODEOWNERSを確認した人間が別途マージします。本番操作はさらに別承認です。

## 21. 完了後の変更

`ai-dev chat "変更内容" --change-for <元Issue> --create-issue`で新Issueを作成し、影響分析から再度進めます。完了済み状態へ要件を直接差し込みません。
