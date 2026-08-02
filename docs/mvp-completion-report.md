# MVP状態報告

## 判定

**MVP foundation complete / Operational MVP incomplete**

設定モデル、CLI基本骨格、MockAgentProvider、SQLite状態管理、決定的状態遷移、初期化・バリデーション、セキュリティ方針、GitHubテンプレート、MockベースWorkflow試験は完成済みである。

実Claudeによる設計・実装、実Issue本文からの一連実行、実commit/push/PR、実Actionsレビュー、実QA統合、実行時Sandbox、実GitHub・実Claude E2Eは外部受入が完了していない。コードまたはMockで再現できる項目も、実外部受入済みとは表現しない。

## 第2回レビュー対応の実装状態

AI変更後のホストVerification、Agent Tool縮小、統合Actions、レビュー前提条件、Finding lifecycle、GitHub event payload安全判定、正式ソースZIP検査を実装した。本報告の「ローカル完了」は実Claude・実GitHub・実Actions受入完了を意味しない。

## 第3回レビュー対応の実装状態

| # | 完了条件 | ローカル状態 | 外部状態 | 判定 |
|---:|---|---|---|---|
| 1 | System Review二重登録解消 | `run_id`冪等保存・QA重複排除試験済み | Actions未実施 | ローカル完了・外部未受入 |
| 2 | 実要件ID単位のTraceability | `RequirementItem`と構造化Issue parser試験済み | 実Issue未実施 | ローカル完了・外部未受入 |
| 3 | QAが全必須要件を確認 | 実装・受入条件別test・review参照の完全性試験済み | 実QA未実施 | ローカル完了・外部未受入 |
| 4 | Claude SDKなしMock CI | SDKを除去したfrozen環境でimport/validate/doctor/test/package成功 | Actions run `30746868976`の3.12/3.13で成功 | 完了 |
| 5 | Python対応版明示 | `>=3.12,<3.14`、CI matrix 3.12/3.13 | Actions run `30746868976`で両版実行成功 | 完了 |
| 6 | Python 3.12/3.13 CI成功 | Workflow定義・YAML試験済み | commit `c7f368c`のActions run `30746868976`で両job成功 | 完了 |
| 7 | 正式Source ZIPクリーン化 | 過去ZIPを含む禁止物拒否・反復生成試験済み | 3.12 jobで生成・検証・Artifact upload成功 | 完了 |
| 8 | Secret scan最適化 | tree除外・明示追跡ファイル検出・ZIP非展開試験済み | 両jobのgitleaks成功 | 完了 |
| 9 | 全追加テスト成功 | ローカル172件成功、1件skip、coverage 80.26% | 3.12/3.13両job成功 | 完了 |
| 10 | README・MVP報告更新 | 更新済み | 文書外部レビュー未実施 | ローカル完了・外部未受入 |

## 第4回レビュー対応の実装状態

| # | 完了条件 | ローカル状態 | 外部状態 | 判定 |
|---:|---|---|---|---|
| 1 | VerificationからTraceabilityを自動捏造しない | 初期recordを空にし、不足時停止を試験 | 実PR未実施 | ローカル完了・外部未受入 |
| 2 | 要件別の設計・実装参照 | DeveloperResult候補を実在path・保護path・commitで検証 | 実Claude未実施 | ローカル完了・外部未受入 |
| 3 | 受入条件と実test caseの対応 | pytest JUnit解析、node ID、PASS限定、commit照合を試験 | Actions未実施 | ローカル完了・外部未受入 |
| 4 | 正式な人間要件承認 | 正規化digest、GitHub投稿者、変更・却下による無効化を試験 | 実Issue未実施 | ローカル完了・外部未受入 |
| 5 | Review対象要件と種別Policy | SYSTEM/BUSINESS/QA対象ID、対象外理由、5要件種別を試験 | 実Review未実施 | ローカル完了・外部未受入 |
| 6 | clean Source Package manifest | dirty拒否、全file hash、package digest、禁止物を試験 | Actions Artifact未実施 | ローカル完了・外部未受入 |
| 7 | 文書・設定・Schema更新 | README、architecture、QA guide、report、root/templateを更新 | 文書外部レビュー未実施 | ローカル完了・外部未受入 |

## 手動Workflow＋承認済みIssue経路

実Claude開発用の`workflow_dispatch`を、承認済みIssue番号だけを入力する経路へ限定した。非公開repository、許可された人間actor、main上のWorkflow定義、入力Issue、実行SHAをGitHub payloadとActions contextの双方から照合する。要件と12件の環境構成は別々のSHA-256 digestに結び付けた人間コメントと`ai:approved`をブランチ作成前に検証する。開発Workflowはホスト検証後のcommit/push/PR作成で停止し、System Review、Business Review、QAはPRイベントの独立Workflowへ委譲する。

Source Packageは指定ディレクトリが`git rev-parse --show-toplevel`と一致することを要求し、pytestのrepository配下temp directoryを誤って有効なpackage rootとして扱わない。ローカル自動テストは実装済みだが、実Claude・実GitHub受入は受入repository準備後に実施する。

## 外部ActionのNode.js 24移行

rootと生成テンプレートの全Workflowで、外部ActionをNode.js 24対応のレビュー済みfull commit SHAへ固定した。commit `3555559`のActions run [`30748749625`](https://github.com/kouitic/ai-development-platform/actions/runs/30748749625)でPython 3.12・3.13の全工程が成功し、Node.js 20廃止予定警告とuvキャッシュ予約競合の注記がないことを確認した。

## Operational MVP 13条件

| # | 条件 | ローカル実装・Mock証拠 | 実外部受入 | 判定 |
|---:|---|---|---|---|
| 1 | AI変更後にホストVerification実行 | 決定的nodeとRunner試験済み | 未実施 | ローカル完了・外部未受入 |
| 2 | Verification成功後だけcommit | Git Gateway拒否・成功試験済み | 未実施 | ローカル完了・外部未受入 |
| 3 | 結果を変更内容へ関連付け | files/diff digest、base/commit SHA、改変無効化試験済み | 未実施 | ローカル完了・外部未受入 |
| 4 | System/Business/QAを重複なく順次実行 | 統合CLIで各1回をMock確認 | Actions未実施 | ローカル完了・外部未受入 |
| 5 | 全レビューが同一PR head SHAを評価 | target bindingとArtifact照合試験済み | 未実施 | ローカル完了・外部未受入 |
| 6 | BusinessはSystem PASSを前提とする | PR/SHA/条件/Finding前提を機械試験済み | 未実施 | ローカル完了・外部未受入 |
| 7 | QAは両レビューとtrusted verificationを前提とする | 自己申告との分離、前提試験済み | 未実施 | ローカル完了・外部未受入 |
| 8 | Findingを種別ごとに追跡・解決確認 | origin/status/evidence/reopen/SHA再評価試験済み | 未実施 | ローカル完了・外部未受入 |
| 9 | 未実装ToolをAgent設定に置かない | validateとClaude拒否試験済み | SDK限定受入未実施 | ローカル完了・外部未受入 |
| 10 | GitHub安全条件を環境変数だけに依存しない | payload/private/fork/branch/workflow試験済み | 実event未受入 | ローカル完了・外部未受入 |
| 11 | クリーンな正式ソースZIP | 生成時・CI用内容検査実装済み | Actions run `30746868976`で3.12/3.13生成・検証成功 | 完了 |
| 12 | Mock E2Eで変更から再検証まで再現 | Verification→commit→PR→rework→再Verification確認済み | 対象外 | Mock完了 |
| 13 | 実Claude＋実GitHubで変更後テスト成功 | 手順・コードあり | 未実施 | **未完了** |

## ローカル検証結果（2026-08-02 JST）

- `ruff format --check .`: 成功（85 files）
- `ruff check .`: 成功
- strict `mypy`: 成功（50 source files）
- `pytest`: 174 passed、1 skipped、coverage 80.26%（基準80%）
- skipはWindowsで実symlink作成権限がないケース。解決済み実体パスが保護対象へ向く回避試験は非skipで成功
- `ai-dev validate`: 成功
- JSON 22件・YAML 34件: parse成功。うちroot/templateのGitHub Actions YAMLは6件
- wheel/sdist build: 成功。2成果物・249 archive entries中、ZIP/cache/build/venv等の混入0件
- `ai-dev package-source`＋`verify-source-package`: 169 files（manifestを含む）、除外対象混入0件、必須source/config/template/test/docs欠落0件
- `pip-audit`: 既知脆弱性0件。ローカルパッケージ自身はPyPI非公開のため監査対象外
- `pip-licenses`: dev＋Claude optionalを含む90パッケージを確認。リポジトリ自身の未選択ライセンス以外に追加判断が必要な結果なし
- 内蔵Secret/Data scan: 検出0件
- ローカルPython 3.13.14で全テスト成功。Actions run `30746868976`のPython 3.12/3.13 matrixも成功

## Mockで確認した範囲

Issue取得、branch、設計・実装Context、Agent自己申告と分離したMockVerification、commit/push、PR、system review major差戻し、Finding付きrework、同一PR再レビュー、business review PASS、QA PASS、GitHub comment/Check、人間承認待ちまでを確認した。critical/major、minor設定、条件付きQA、GitHub失敗、Schema不適合、不正Evidence、Finding保持、環境未回答、main/force/merge、path/symlink、shell injection、env列挙、network違反も試験した。

## 実外部接続で確認した範囲

GitHub Actions CIはcommit `c7f368c`のrun `30746868976`で3.12/3.13とも成功した。さらに、Node.js 24対応Actionへ移行したcommit `3555559`のrun [`30748749625`](https://github.com/kouitic/ai-development-platform/actions/runs/30748749625)でも両版の全工程が成功した。実GitHub Issue/branch/commit/push/PR/comment/Checkと、Claude Agent SDK tool callback/sandboxは未受入である。

## 完成済みのfoundation

- 設定モデル、CLI骨格、Mock Agent、SQLite、監査イベント
- 決定的なWorkflow、反復上限、pause/resume/cancel
- 安全な初期化、設定・Schema・Secret/Data validation
- GitHub Issue/PR/ActionsテンプレートとMock試験

## 未完成または未受入

- 実Claudeの設計・コード変更と制限付きtool実行
- 実GitHubのIssue、branch、commit、push、PR、comment、Check
- Actions上のsystem/business/QAの成功・失敗受入
- 実環境でのsymlink、sandbox、network proxyを含むPolicy受入
- branch protection、Secret scanning、Push protection、Environment/IAMの管理者設定
- 本番相当データを一切持ち込まない限定E2E

## 残存リスク

Claude SDK sandboxのOS差、GitHub権限とforkイベント、外部サービス障害、Issue本文への機密情報混入、AIによる不十分な修正主張が残る。Gateway失敗は進行停止し、Findingは再レビューPASSまで保持するが、最終判断は人間が対象Issue・PR・stage・SHA・GitHub記録を確認して行う。
