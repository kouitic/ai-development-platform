# ai-development-platform

人間の業務判断と明示承認を保ちながら、複数の専門AIによる要件整理、設計・実装、システムレビュー、業務レビュー、QA評価を進めるPython製CLIとGitHubテンプレートです。

AIは品質証拠を収集し、基準を満たした場合に「定義された品質基準を満たしています」と報告します。最終的な品質保証、残存リスクの受容、mainへのマージ、本番デプロイ、本番データ変更は人間の責任です。

## 二つのリポジトリ規約

このリポジトリでは、プラットフォーム自体を人間とCodexが保守する規約（パターン①）と、プラットフォームが対象システムのAI開発へ強制する規約（パターン②）を分離します。

- パターン①では、人間の明示指示によりmainへの直接コミット・プッシュを許可し、Issue、作業ブランチ、PRを必須としません。実行規約はルート`AGENTS.md`です。
- パターン②では、承認済みIssue、作業ブランチ、PR、独立レビュー、QA、人間によるmainマージ承認を必須とし、設計開発AIによるmain操作を禁止します。

境界、優先順位、具体例は[リポジトリ規約の適用範囲](docs/repository-governance.md)を参照してください。以下で説明するmain保護と人間承認は、特記がない限りパターン②の製品動作です。GitHub Actionsの固定内容と更新方法は[外部Action固定台帳](docs/github-actions-pinning.md)に記録します。

## 現在の判定

**MVP foundation complete / Operational MVP incomplete**

ローカル実装とMock試験は利用できますが、実Claude＋実GitHub＋GitHub Actionsの限定受入は未実施です。「実装済み」「Mockでシミュレート可能」「外部受入済み」を同じ意味で扱いません。

## 現在の実装

- `ai-dev init / validate / doctor / chat / ask / run / status`
- `deployment-questions / deployment-answer / approve-deployment`
- `review / qa / quality-gates / verify-commit / pause / resume / cancel / approve / reject / logs / package-source / verify-source-package`
- 対話、設計開発、システムレビュー、業務レビュー、QAのYAML定義
- AgentProvider、ClaudeAgentProvider、MockAgentProvider
- LangGraphと決定的な状態遷移Guard
- 最大3回の差戻し、予算上限、人間承認待ち
- SQLite状態・監査・承認、工程境界停止、再開
- GitHub Issue/PR/diff/files/comment/label/Check GatewayとGitHub Flowテンプレート
- stage別Schema、要件ID・設計・実装・JUnit test case・受入条件・必須Reviewを結ぶホスト検証済みTraceability、TaskEvidence、Finding lifecycle、QA証拠統合
- AI変更後のworktree digestへ結び付いたホストVerification成功後だけcommit→作業branch push→PRを行うGit Gateway
- Secret、本番相当データ、保護パス、argv、networkの実行時Policy
- CI→system→business→QAを同一PR head SHAで一度ずつ実行する統合GitHub Actions、CODEOWNERS、pre-commit雛形

mainマージと本番操作を行うAPIはMVPに含めていません。

## Mockモード

- `provider: mock`: AI結果を決定的に生成し、外部LLMを呼びません。
- `github.gateway: mock`: Issue、branch、commit/push完了、PR、comment、Checkをメモリ上で再現します。
- Mock＋Mockでは、Issue取得→環境回答待ち→設計・実装→ホストMock Verification→commit/push→PR→system review差戻し→修正→再Verification→business review→QA→人間承認待ちを外部APIなしで試験できます。
- Mock成功は、実ClaudeやGitHubの受入済みを意味しません。

## 実Claudeモード

`AI_DEV_PROVIDER=claude`を選ぶと、`GITHUB_EVENT_PATH`の実payloadとActions contextからPrivate repository、非fork、同一head repository、許可branch、pull_request event、actor、統合Workflowを検証します。自己申告の`AI_DEV_MINIMAL_PERMISSIONS`等だけでは起動できません。統合Workflow YAMLにGitHub Environmentやproduction用途がないことも検査します。Claude SDKにはRead/Glob/Grep/Write/Editと必要なread-only Webだけを渡し、テスト、Git、PR、comment、Checkはホスト側サービスが担当します。

この接続コードは実装済みですが、実Claude限定受入は未実施です。

## Mock GitHub Gatewayと実gh Gateway

`MockGitHubGateway`は外部API不要の単体・E2E試験用です。`GhCliGateway`は`gh`を引数配列で起動し、Issue/PR/Checkを実GitHubへ記録します。merge APIはありません。承認はGitHub comment/review IDを得られなければSQLiteへ記録されません。

実gh Gatewayの限定受入は未実施です。

## 必要環境

- Windows 11 + PowerShell、またはLinux/macOS
- Python 3.12または3.13（`>=3.12,<3.14`）
- uv
- Git
- 実GitHub連携時のみGitHub CLI (`gh`)
- 実Claude連携時のみ`claude` extraとClaude Agent SDKの認証

## セットアップ

```powershell
uv sync --frozen --extra dev
uv run ai-dev doctor
uv run ai-dev validate
uv run pytest
```

Mock用の通常環境にはClaude Agent SDKを導入しません。実Claude連携時だけ`uv sync --frozen --extra dev --extra claude`を使用します。

Linux/macOSでも同じ`uv`コマンドを使用できます。詳細は[セットアップガイド](docs/setup-guide.md)を参照してください。

## 最小利用例

新しい空のリポジトリで次を実行します。

```powershell
uv run ai-dev init sample-system
uv run ai-dev doctor
uv run ai-dev validate
uv run ai-dev chat "申請の承認漏れを担当者へ通知したい"
uv run ai-dev run --issue 1 --commit-sha <対象コミットSHA>
uv run ai-dev status --issue 1
```

Issue Formの構造化YAMLは正式要件の候補ですが、それだけでは承認済みになりません。正規化した要件digestと一致する人間のGitHub承認コメントがある場合だけ承認済みとし、Issue本文の変更でdigestが変われば再承認まで停止します。自然言語IssueからAIが生成した要件候補も同じく`REQUIREMENTS_APPROVAL_REQUIRED`で停止します。その後、未確定のデプロイ・環境構成があれば停止します。

```powershell
uv run ai-dev approve --issue 1 --stage requirements --commit-sha <対象コミットSHA> --approver <承認者>
uv run ai-dev run --issue 1
uv run ai-dev deployment-questions --issue 1
uv run ai-dev deployment-answer --issue 1 --question-id <項目ID> --answer <回答> --answered-by <回答者>
uv run ai-dev approve-deployment --issue 1 --approver <承認者>
uv run ai-dev run --issue 1
```

実Claude＋実GitHubでは、ローカル状態をRunner間で持ち回りません。Issue Formへ構造化要件と12件の`deployment_answers`を記入し、次のコマンドが表示する二つのダイジェスト付きコメントを人間がIssueへ投稿して、`ai:approved`を付与します。

```powershell
uv run ai-dev issue-approval-template --issue 1
uv run ai-dev issue-preflight --issue 1
```

`.ai-dev/project.yaml`のGitHub連携を有効にし、`allowed_actors`へ手動実行を許可する人間を設定したうえで、Actions画面からmainの`AI開発オーケストレーター`へIssue番号を入力します。Workflowは承認をブランチ作成前に再検証し、Claude開発、ホスト検証、Issueブランチへのcommit/push、PR作成まで進んで停止します。System Review、Business Review、QAは、そのPRイベントで起動する独立した`ai-quality-gates.yml`が担当します。

両レビューとQAが完了し、人間承認待ちへ到達した後、証拠を確認した人間だけが次を実行します。

```powershell
uv run ai-dev approve --issue 1 --stage human-approval --commit-sha <対象コミットSHA> --approver <承認者>
```

この承認はGitHub正式記録の成功後にローカル監査へ記録しますが、mainのマージや本番操作は行いません。

正式なrequired Check経路は`.github/workflows/ai-quality-gates.yml`です。CI検証、System Review、Business Review、QAを同一Job・同一PR head SHAで順番に一度ずつ実行し、各結果JSONとSHA-256 digestを安全な一時領域へ保存します。

```powershell
uv run ai-dev quality-gates --issue 1 --pr 2 --base-sha <PR base SHA> --head-sha <PR head SHA>
```

単独の`review`・`qa`は診断用に残しています。実GitHubで使用する場合は、`verify-commit`が生成したdigest付き`--verification-result`が必須です。pytestにはホスト管理の`--junitxml`を付与し、実行されたtest caseを取得します。Agentは要件と設計・実装・pytest node IDの対応候補を提示できますが、対象commitに存在するファイルと実行済みPASS testだけが正式証拠になります。Verification全体のPASSだけで全受入条件を満たしたことにはしません。

`package-source`で生成し、`verify-source-package`で検査したZIPだけを正式提出物とします。正式生成にはcleanなGit状態を必須とし、commit SHA、生成時刻、全収録ファイルのSHA-256、package digestを`source-package-manifest.json`へ記録します。既存ファイルは上書きせず、source/config/template/test/docsだけを含め、`.git`、`.venv`、cache、build、coverage、egg-info、過去の`*.zip`を拒否します。CIはPython 3.12・3.13の両方でMock-only frozen installと品質検査を行い、3.12 jobが正式ZIPをArtifactとして生成します。

```powershell
uv run ai-dev package-source --output ai-development-platform-source.zip
uv run ai-dev verify-source-package --archive ai-development-platform-source.zip
```

## 設計資料

- [要件分析](docs/requirements-analysis.md)
- [アーキテクチャ](docs/architecture.md)
- [MVP範囲](docs/mvp-scope.md)
- [実装計画](docs/implementation-plan.md)
- [ロードマップ](docs/roadmap.md)
- [セキュリティモデル](docs/security-model.md)
- [データガバナンス](docs/data-governance.md)
- [開発ワークフロー](docs/development-workflow.md)
- [実Claude＋実GitHub限定受入ガイド](docs/limited-acceptance-guide.md)

## 外部受入済み機能

現時点ではありません。ローカルMock試験、lint、型検査、Schema検証は外部受入と区別します。

## 実運用前に必要な設定

- Private GitHub repository、branch protection、required Checks、CODEOWNERS
- Secret scanning、Push protection、GitHub Environment/IAMの確認
- `gh`認証と最小権限token
- Anthropic Secret、費用上限、許可branch、fork拒否
- デプロイ先、利用者、RTO/RPO、費用、運用担当、本番相当データ禁止方針の人間承認
- 限定テストIssue/PRでの受入手順完了

## 現在の制約

- main merge、本番deploy、本番data変更、rollbackは実行しません。
- 本番相当データを通常AI、Git、Issue/PR、Artifact、ログ、SQLiteへ渡せません。
- Claude SDK sandboxのOS差とGitHub管理設定はローカル単体試験だけでは保証できません。
- 会話の実AIモードはProvider経由の質問・stage実行で、完全なWeb UIや長期マルチテナント会話は対象外です。

## セキュリティ上の注意

`.env`、APIキー、token、接続文字列、秘密鍵をGitへ追加しないでください。本番相当データはGit、Issue、PR、Artifact、通常ログ、AI会話、LangGraph State、通常SQLiteへ保存できません。検出時は値やデータ本文を再掲せず、安全停止します。

## ライセンス

Private useを前提としており、ライセンスは未選択です。外部公開・配布前にライセンスを決定し、`uv run pip-licenses --format=markdown`で依存ライセンスを再確認してください。
