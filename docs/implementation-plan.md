# 実装計画

## 第3回レビュー対応

| 区分 | 内容 | ローカル状態 | 外部状態 |
|---|---|---|---|
| R3-1 | Review `run_id`冪等保存とQA Context重複排除 | 実装・回帰試験済み | 実Actions未受入 |
| R3-2 | `RequirementItem`、構造化Issue、要件承認待ち、完全Traceability | 実装・Mock試験済み | 実Issue未受入 |
| R3-3 | Claude SDK optional extra、Mock-only frozen install | 実装・SDKなしローカル試験済み | Actions run `30746868976`で3.12/3.13成功 |
| R3-4 | Python `>=3.12,<3.14`、3.12/3.13 CI matrix | 定義・YAML試験済み | Actions run `30746868976`で両版成功 |
| R3-5 | ZIP入れ子拒否、scanner対象最適化、gitleaks役割分離 | 実装・ローカル試験済み | Artifact生成・検証・gitleaks成功 |
| R3-6 | 外部Action固定方針 | Node.js 24対応release、input、署名をレビューしfull SHA固定 | 更新後Actionsで再確認予定 |

指摘ごとの原因、影響、変更対象、試験、完了条件は`docs/third-review-analysis.md`に固定した。

## 第2回レビュー対応（論理PR）

作業場所に`.git`がないため実PR・commitは作成できない。次の責務単位で変更と試験を分離した。

| 論理PR | 内容 | ローカル状態 | 外部状態 |
|---|---|---|---|
| PR 1 | VerificationRunner、決定的`AUTOMATED_TESTING`、digest・commit関連付け | 実装・Mock/Local試験済み | 実AI変更後の実行未受入 |
| PR 2 | Agent Tool縮小、Verification Policy、validate強化 | 実装・試験済み | Claude SDK限定受入未実施 |
| PR 3 | `ai-quality-gates.yml`統合、前提条件、JSON/digest、Check名 | 実装・Mock試験済み | Actions未受入 |
| PR 4 | Finding origin/status/解決証拠/再発/SHA再評価 | 実装・試験済み | 実PR未受入 |
| PR 5 | GitHub payload安全判定、正式Source ZIP検査、文書 | 実装・試験済み | 実GitHub未受入 |

指摘ごとの原因・影響・変更対象・試験・完了条件は`docs/second-review-analysis.md`に固定した。

## 実装レビュー対応Issue

| Step | Issue | 主な受入条件 | 状態 |
|---|---|---|---|
| 1 | OPM-16 Gateway・証拠契約 | Issue/PR/diff/files/comment/labels/Check、個別Schema、TaskEvidenceがMock試験可能 | ローカル完了・外部未受入 |
| 2 | OPM-17 Stage Context・Rework・QA | Issue本文、受入条件、PR差分、Finding、全証拠が該当AIへ渡り、不足時は進行しない | ローカル完了・外部未受入 |
| 3 | OPM-18 安全なGit公開 | test→commit→work-branch push→PRの順序、main/force/merge拒否 | ローカル完了・実Git未受入 |
| 4 | OPM-19 Actions品質ゲート | system/business/QA CLI、Schema、PR要約、Check failure、外部API不要のMock | ローカル完了・Actions未受入 |
| 5 | OPM-20 デプロイ対話 | 12項目の平易な質問、回答再質問防止、人間承認待ち、Context反映 | ローカル完了・外部未受入 |
| 6 | OPM-21 Runtime Policy | path/symlink/credential、argv allowlist、network policy、SDK permission callback | ローカル完了・SDK限定受入未実施 |
| 7 | OPM-22 配布・限定受入 | README、限定受入手順、source-only ZIP、15条件の最終評価 | 文書・ZIP完了、限定受入未実施 |

各Issueの完了にはformat、lint、mypy、全pytest、coverage、validate、Secret/Data scanを要求する。Mockの成功は外部受入済みを意味しない。実Claude、実GitHub、Actions、管理者設定を伴う証拠は、限定Privateリポジトリで別に取得する。

## Phase 0: 設計確定

1. 要件、不明点、制約、リスクを整理する。
2. レイヤー、ワークフロー、Provider、状態保存、安全制御を設計する。
3. MVP境界とPhase 2/3の拡張点を固定する。
4. ADRとIssue分割案を作成する。

完了条件は、指定された事前文書が揃い、MVPを妨げる重大な矛盾がないことである。

## Phase 1: MVP土台

1. `pyproject.toml`、パッケージ、CLIエントリポイントを作成する。
2. Pydantic設定モデルと静的JSON Schemaを作成する。
3. AgentProvider、Mock、Claude adapterを作成する。
4. 安全なテンプレート生成と競合停止を実装する。
5. `validate`と`doctor`を実装する。
6. 状態モデル、遷移Guard、SQLite Store、LangGraph組立てを実装する。
7. Mockワークフロー、停止・再開・承認待ちを実装する。
8. 対話とIssue/GitHub Gatewayを実装する。
9. Secret/Data Guard、構造化監査ログを実装する。
10. GitHubテンプレートとActionsを整備する。
11. 自動テストと利用文書を完成させる。

## 実装順の理由

安全ポリシーとSchemaを先に固定し、外部接続より先にMockの全フローを成立させる。これにより、Claude/GitHubの資格情報がないCIでも状態遷移と承認ゲートを検証できる。外部アダプターは最後に契約へ接続する。

## MVP Issue分割案

| ID | Issue | 受入条件 |
|---|---|---|
| MVP-01 | Python/uvプロジェクト土台 | Python 3.12、`ai-dev --help`、品質ツールが動く |
| MVP-02 | 設定モデルとJSON Schema | 有効設定を読め、不正YAML・Schema違反を拒否する |
| MVP-03 | AgentProvider | Mockで成功/失敗/不正出力/timeoutを再現できる |
| MVP-04 | 安全な`init` | 必須ファイルを生成し、既存ファイル競合時に無変更で停止する |
| MVP-05 | `validate` | 権限、保護パス、workflow、Secret、データ分類違反を検出する |
| MVP-06 | `doctor` | 必須ツールと設定状態をSecret非表示で診断する |
| MVP-07 | 状態とSQLite | 状態・監査・承認を保存し、中断後に再読込できる |
| MVP-08 | Workflow | 正常系、各差戻し、3回BLOCKED、pause/resume/cancelを再現する |
| MVP-09 | 対話とIssue構造化 | 自然言語からIssue案を構造化し、通常表示は業務影響中心となる |
| MVP-10 | GitHub Gateway | Issue/ブランチ/コメント操作を抽象化し、API失敗を扱う |
| MVP-11 | 承認ゲート | stage/SHA一致を要求し、曖昧承認と変更後承認を拒否する |
| MVP-12 | Secret/Data Guard | 検出、マスク、保存拒否、インシデント遷移を試験できる |
| MVP-13 | GitHub templates/CI | Issue/PR/Actions/CODEOWNERSを最小権限で提供する |
| MVP-14 | CLI統合 | 指定MVPコマンドと対話中コマンドが利用できる |
| MVP-15 | 文書とE2E | setupからMockフロー、人間承認待ちまで再現できる |

## Phase 2 Issue分割案

| ID | Issue |
|---|---|
| P2-01 | デザインレビューAIと出力Schema |
| P2-02 | リリース・運用設計AIと出力Schema |
| P2-03 | UI規約・デザインシステム・Storybook連携 |
| P2-04 | アクセシビリティとスクリーンショットレビュー |
| P2-05 | 複数環境モデルとGitHub Environments |
| P2-06 | 環境別品質ゲートと昇格条件 |
| P2-07 | リリース・ロールバック・DB移行戦略 |
| P2-08 | 本番相当データ専用検証ジョブ |
| P2-09 | 品質メトリクス、バグ収束、すり抜け分析 |
| P2-10 | 実クラウドデプロイGateway |

## Phase 3候補一覧

- 競合検出、保護パスLock、並列コード変更
- 実行中要件のversion管理と差分適用
- 複数ProviderとProvider routing
- 複数リポジトリ/組織ポリシー
- 長期品質分析、コスト最適化
- 本番自動化と段階リリース
- Canary、Blue/Green、Feature Flag、Preview環境

## 検証計画

各Issueでunit testを追加し、CLI統合後にMock E2Eを実行する。外部接続テストは資格情報を要求する任意テストへ分離する。完了時はformat、lint、type check、全pytest、coverage、secret scanを実行する。
