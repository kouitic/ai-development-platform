# アーキテクチャ

## レビュー対応アーキテクチャ

実行経路は `GitHubGateway`、`GitWorktreeGateway`、`VerificationRunner`、`TaskContextBuilder`、stage別結果モデル、`TaskEvidence`、`WorkflowRunner` の順に責務を分離する。Issue/PR本文は未信頼データとして収集し、Secret検査後にstage別の最小コンテキストへ変換する。AIが生成したshell文字列を実行するAPIは設けない。

Git変更は、許可パスと保護パス確認、変更ファイル・diff digest取得、ホストVerification PASS、対象不変確認、commit、検証結果とcommit SHAの関連付け、作業ブランチpush、PR作成の順序を専用Gatewayが強制する。AI自己申告テストは`agent_reported_test_results`、ホスト証拠は`trusted_verification_results`として分離する。main push、force push、merge、reset hard、clean、PR mergeは公開APIに存在しないか実行時に拒否する。

品質結果は要件、デプロイ、開発、システムレビュー、業務レビュー、QAの個別Schemaで検証し、SQLiteには安全な集計と参照IDだけを保存する。正式要件は`RequirementItem`のIDで管理する。Developer AIの設計・実装・test case対応は候補として受け取り、リポジトリ内の実在パス、保護パス承認、対象commitのファイル、JUnitに存在するPASS test caseをホスト側で検証してから`TraceabilityRecord`へ昇格する。Verification全体の成功から全要件の証拠を自動生成しない。各必須要件について設計参照、実装参照、各受入条件の実行済みPASS test、要件種別ごとの必須Reviewが揃わなければQAを起動しない。AI生成要件は`REQUIREMENTS_APPROVAL_REQUIRED`、QAの条件付き合格は`QA_CONDITIONAL_APPROVAL_REQUIRED`、未確定の環境構成は`DEPLOYMENT_CONFIGURATION_REQUIRED`で停止する。

Claude Agent SDKにはtool permission callbackとsandbox設定を渡す。ファイルは実体パスへ正規化し、リポジトリ外、symlink回避、資格情報、未許可書込みを拒否する。SDKのshell toolは文字列契約であるため使用を拒否し、ホスト側の安全なGit・テストAPIへ分離する。Webはallowlistまたはread-only判定を通し、送信・認証・実行・機密データ送信を許可しない。

## 1. 方針

単一プロセスのCLIを中心に、ドメイン、アプリケーション、インフラを分離する。通常利用で常駐サーバーは不要とし、GitHubとClaudeへの接続はアダプターの背後に置く。安全上の判定はプロンプトではなく、設定検証、権限、状態遷移、保護パス、承認、外部プラットフォーム設定を重ねて強制する。

## 2. コンポーネント

```text
Human
  │
  ▼
Typer CLI / Prompt Toolkit chat
  │
  ▼
Application services ─────────────── Audit service
  │              │                         │
  │              ├─ Policy / Approval      ▼
  │              ├─ Secret/Data guards   SQLite
  │              └─ GitHub use cases
  ▼
Workflow Orchestrator (LangGraph + deterministic transition guard)
  │
  ├─ AgentProvider ─ ClaudeAgentProvider
  │                 └ MockAgentProvider
  ├─ GitHubGateway ─ GhCliGateway
  │                 └ MockGitHubGateway
  ├─ VerificationRunner ─ LocalVerificationRunner
  │                      └ MockVerificationRunner
  └─ ProjectConfig / JSON Schema validator
```

## 3. 責務分離

- `domain`: 状態、判定、承認、Agent要求・結果、レビュー、データ分類。外部ライブラリへの依存を最小化する。
- `application`: ユースケース、オーケストレーション、再作業制御、対話の構造化、GitHub操作の順序制御。
- `infrastructure`: YAML、SQLite、`gh` CLI、Claude Agent SDK、ファイルシステム、ログ。
- `interfaces`: Typerコマンドと対話表示。安全判定そのものは持たない。

## 4. Agent Provider

`AgentProvider.execute(AgentRequest) -> AgentResult`を共通契約とする。入力には役割、プロンプト、構造化コンテキスト、許可能力、出力Schema、タイムアウトを含める。結果は構造化payload、利用量、モデル、終了理由を持つ。

`AgentRequest.output_schema`はホスト側の正式契約とし、外部Providerへ送る転送Schemaとは分離する。Claudeでは、固定された`result_json`文字列エンベロープだけをStructured Outputsへ渡し、復号後のobjectを正式Schemaで再検証する。これにより、外部APIの任意項目数や未対応Schema keywordの制約をドメインモデルへ波及させない。詳細は[Agent Provider連携IF仕様](provider-interface.md)を参照する。

- `MockAgentProvider`: テストシナリオをキューから返し、外部接続なしで全遷移を再現する。
- `ClaudeAgentProvider`: `claude` optional extraのClaude Agent SDKを遅延importし、設定されたツールとターン上限で実行する。Mock経路はSDKを依存に持たず、Claude選択時だけSDK未導入や資格情報不足をSecret非表示で報告する。
- 将来Providerは登録表へ追加し、ドメインとワークフローを変更せず選択できる。

## 5. LangGraph Workflow

LangGraphのグラフは各工程の実行順を表す。各ノードのAI結果はPydantic/JSON Schemaで検証し、別の`TransitionGuard`が現在状態、判定、反復数、承認、インシデントを確認する。グラフノードが直接任意状態を書き換えることはできない。

主経路は次のとおり。

```text
NEW → REQUIREMENTS_ANALYSIS → DEPLOYMENT_CONFIGURATION → PLANNING
→ DESIGNING → IMPLEMENTING → AUTOMATED_TESTING（ホスト決定工程）→ SYSTEM_REVIEW
→ BUSINESS_REVIEW → QA_ASSESSMENT → HUMAN_APPROVAL_REQUIRED
```

失敗時は`REWORK_REQUIRED`から`IMPLEMENTING`へ戻る。反復が3回に達した場合は`BLOCKED`とする。Secret検出は`SECURITY_INCIDENT_REQUIRES_HUMAN`、データ漏えい可能性は`DATA_EXPOSURE_REQUIRES_HUMAN`へ優先遷移する。pause/cancelは安全なノード完了後に反映する。

`AUTOMATED_TESTING`はAgent stageではない。Developer AIはテスト追加、失敗原因分析、修正を担当し、実行と成否判定はVerificationRunnerがargv配列で行う。検証後にdiff、変更ファイル、基準SHAが変われば結果を`INVALIDATED`にする。

pytest実行時はVerificationRunnerが一意な`--junitxml=.ai-dev/local/test-results/<run-id>.xml`を付与する。JUnit XMLはホストが解析し、node ID、file、PASS/FAIL/SKIP/ERROR、所要時間、証拠参照を`ExecutedTestCase`として対象commitの`VerificationResult`へ保存する。重複node IDは最悪の結果を採用し、未実行、SKIP、FAIL、ERRORを受入条件の成功証拠にしない。

Local VerificationのSecret検査は変更ファイルと必須設定ファイルを明示パスで高速走査する。生成物ディレクトリの全tree走査は行わない。一方、CIはgitleaksでGit履歴と追跡対象全体を検査し、tree除外配下に追跡されたファイルも対象にする。

## 6. GitHub連携

`GitHubGateway`はIssue作成、Issue取得、ブランチ作成、コメント投稿、ラベル更新に限定する。mainへのpush、PRマージ、本番デプロイAPIはMVP Gatewayに公開しない。実装は引数配列で`gh`を呼び出し、shell展開を避ける。MockによりAPIエラーと冪等な再実行を試験する。

正式品質ゲートの変更ファイル一覧とPR差分本文は、GitHub APIのファイル件数・差分行数制限に依存させない。ホストVerificationと同じbase/head SHA、cleanなcheckout、正規化済み変更パスからローカルGitで完全取得し、変更ファイル一覧とdiff digestが`VerificationResult`に一致する場合だけReview Contextへ渡す。GitHubから取得するPR head SHAとの不一致、worktree変更、一覧欠落、digest不一致は安全側で停止する。

Python 3.12/3.13のCI証拠は、同じhead SHAに結び付いたGitHub Check Runsからホストが取得する。設定された必須Checkがすべて`completed/success`になるまで上限時間内で待機し、Check Run ID、名前、対象SHA、完了時刻、GitHub URLだけをdigest付きの`ci-evidence.json`へ保存する。失敗、未完了、欠落、別SHAの結果は正式レビューへ渡さない。検証済みCI証拠はSystem ReviewのContextと`TaskEvidence`へ明示的に含め、Business ReviewとQAにも同一証拠を引き継ぐ。

確定要件と正式結果だけをGitHubへ同期する。未確定会話、Secret、本番相当データは同期しない。

## 7. SQLite状態管理

`.ai-dev/local/state.sqlite3`にタスク、イベント、承認、エージェント実行メタデータを保存する。payloadは保存前にSecret/Data Guardを通す。状態更新と監査イベント追記は同一トランザクションで行い、楽観的versionで二重更新を拒否する。

主要テーブルは`tasks`、`audit_events`、`approvals`、`agent_runs`。会話本文の保存は必要最小限とし、Secret検出時は本文を保存しない。

## 8. ターミナル対話

`chat`は自然言語を対話AIへ渡し、要件候補、未決定事項、Issue案を構造化する。通常表示は業務影響中心とし、`/technical-details`でのみ技術情報を展開する。`@agent`または`ask`の回答は情報提供に限定し、正式レビューや状態を変更しない。

## 9. Secretと本番相当データ

- ファイルアクセス前に禁止パスを正規化して検査する。
- Providerへ渡すcontextはData Guardで分類し、役割別に縮退する。
- ログハンドラーは既知Secret値と代表的なSecret形式をマスクする。
- Git候補ファイルはSecret/Data Scannerで検査し、検出時は停止する。
- 本番相当データは通常のファイル、SQLite、会話、Artifactへ格納しない。

詳細は`security-model.md`と`data-governance.md`を参照する。

## 10. 承認管理

承認は`issue_number`、`stage`、`commit_sha`、`approver`、`approved_at`を持つ。要件承認はさらに、要件ID・種別・説明・受入条件・必須性を正規化したSHA-256 digestとGitHubコメント参照を`RequirementsApproval`へ保持する。手動開発Workflowは、12件の環境回答を正規化した別のSHA-256 digestと人間コメントも検証する。構造化Issueや`ai:approved`ラベルだけでは承認済みにせず、botではないGitHub投稿者による所定形式の両コメント、現在digest、ラベルがすべて一致する場合だけ有効とする。Issue変更、対象SHA変更、後続の却下で無効になる。曖昧な自然言語は承認レコードに変換しない。mainマージ、本番操作、ロールバックはCLIの情報提供対象にはできるが、自動実行経路はMVPに設けない。

手動開発Workflowはこの承認をブランチ作成前に再構成し、同一の一時Runner内で要件・環境構成証拠をSQLiteへseedする。これにより、Issue本文などの詳細をSQLite ArtifactでRunner間転送しない。PR作成後は`PAUSED`で終了し、独立したPR品質Workflowがcommit SHA固定でレビューを開始する。

## 10.1 Review Coverage Policy

`ProjectConfig.review_coverage`はBUSINESS、FUNCTIONAL、NON_FUNCTIONAL、SECURITY、OPERATIONALごとにSYSTEM、BUSINESS、QAの必須組合せを定義する。各Review結果は`evaluated_requirement_ids`と`excluded_requirement_reasons`を返す。ホストは未知・重複ID、必須対象の欠落、理由のない対象外を拒否し、`review:<type>:<run-id>`を実際に評価した要件だけへ登録する。

## 10.2 正式Source Package

正式Source ZIPはcleanなGit commitからだけ生成する。`source-package-manifest.json`にcommit SHA、clean判定、生成時刻、各source fileのSHA-256、正規化ファイル一覧のpackage digestを格納し、検証時にZIP内のパス、重複、symlink、禁止物、ファイルhash、digestを再計算する。`.git`、`.venv`、過去ZIP、cache、build生成物は収録しない。

## 11. 監査ログ

状態遷移、検証、Agent開始・終了、外部操作、承認・却下、pause/cancel、セキュリティ停止をJSON LinesとSQLiteイベントに記録する。Secret値、会話全文、本番相当データは記録しない。イベントID、時刻、task、actor、action、結果、相関IDを残す。

## 12. テスト構成

- Unit: モデル、遷移、ポリシー、scanner、設定merge、承認
- Integration: temp directoryへのinit/validate、SQLite再開、Mockフロー、CLI
- Contract: Provider/GitHub GatewayのMock契約、不正JSON、timeout、API failure
- CI: format、lint、型、pytest、coverage、secret、dependency、license

## 13. WindowsとLinux

パスは`pathlib.Path`、エンコーディングはUTF-8、プロセスは引数配列で実行する。PowerShell固有処理をドメインへ持ち込まない。GitHub ActionsはUbuntuで実行し、Windows固有の利用手順は別記する。

## 14. 拡張ポイント

- Provider registry
- Agent definitionの追加と出力Schema URI
- Workflow node registryとreview type
- GitHub/State Store adapter
- 品質ゲートと環境昇格ポリシー
- デザイン、リリース・運用の追加AI
- controlled parallelのLock Manager

追加機能は上位禁止ポリシーを緩和できない。
