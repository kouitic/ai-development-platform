# 第2回レビュー指摘の事前分析

## 実施条件

この文書は実装開始前の原因分析と受入基準を固定する。作業場所には`.git`が存在しないため、指定されたPR 1〜5は実PRやcommitではなく、変更責務と試験を分離した論理PR単位として扱う。実GitHub、実Claude、実Actionsで確認できない項目は完了にしない。

## 指摘別分析

| 論理PR | 指摘 | 原因 | 影響 | 修正方針 | 主な変更対象 | テスト方法 | 完了条件 |
|---|---|---|---|---|---|---|---|
| PR 1 | AI変更後の信頼できる検証 | Workflow開始前にCLIが`trusted_test_results`を生成し、`AUTOMATED_TESTING`をDeveloper Agent stageとして実行していた | 変更前または自己申告の成功結果でcommitでき、対象コードとの結び付きがない | ホスト管理の`VerificationRunner`を導入し、変更ファイルとdiffのdigestを検証前後で照合する。成功結果だけをcommitへ渡し、commit後SHAを関連付ける | domain model、verification service、workflow、Git gateway、CLI、設定 | 変更前結果拒否、変更後実行、改変時無効化、失敗時commit拒否、自己申告PASS拒否、Mock E2E | `trusted_verification_results`だけでcommit・review・QAが進み、対象digestとcommit SHAを追跡できる |
| PR 2 | Agent ToolとRuntime Policyの不整合 | YAMLに`Test`、`GitHubIssue`、`GitHubComment`、`executable_commands`がある一方、Claude runtimeに対応実装がない | 設定上の能力と実行時能力が異なり、安全境界を誤認する | Agent Toolを`Read/Glob/Grep/Write/Edit`へ限定し、テスト・Git・GitHub操作はホストサービスへ移す。検証コマンドはVerification Policyへ移す | Agent model/schema/YAML、template、validator、Claude provider | 未実装Tool拒否、YAML全Tool実装確認、ClaudeによるTest/GitHub拒否、Verification allowlist確認 | `validate`が宣言と実装の差を検出し、Agent requestへホスト専用操作を渡さない |
| PR 3 | GitHub Actions重複とReview前提不足 | system/business/QAが別Workflowで並列起動し、QA内でも前2レビューを再実行していた。前提判定はDecision中心でPR/SHA/条件を十分確認していない | 重複実行、Check上書き、RunnerローカルSQLite分断、異なるSHAの証拠混入が起こる | `ai-quality-gates.yml`へ一本化し同一job・同一PR head SHAで順次実行する。各結果を安全なJSON envelopeへ保存しdigest・PR・SHAを検証する | Actions、quality gate、artifact service、CLI、GitHub Check | 各stage一回、同一PR/SHA、前提欠落拒否、artifact digest/SHA不一致拒否 | 正式required Check経路が統合Workflowだけになり、Business/QA前提が機械的に強制される |
| PR 4 | Finding解決・再発管理 | Findingにorigin、状態、候補SHA、解決reviewの情報がなく、Developerのclaim後に任意のPASSレビューで暗黙削除していた | 異なるreview種別が指摘を消せる。acceptance test未確認やSHA変更後も解決扱いになり得る | 状態付きFinding lifecycleを実装し、origin reviewだけが、対象SHAとacceptance evidenceを確認してRESOLVEDへ遷移できるようにする | Finding model、workflow、context、schemas、tests | 自己申告非解決、origin制限、証拠必須、REOPENED、SHA再評価 | Findingを削除せず履歴保持し、状態と解決証拠でblocking判定できる |
| PR 5 | GitHub安全判定・Check・ZIP | Claude起動安全性を自己申告環境変数へ依存し、Check名・内容とZIP CI検査が不足していた | fork/public/不正branchからSecret付き実行、Check衝突、汚染ZIP提出の危険がある | GitHub event payloadとActions contextを構造検証し、Mock以外のoverrideを禁止する。Check名を固定し構造化要約を含める。正式ZIPをCIで検査する | provider factory、GitHub context model、Actions、package service、README・完了報告 | env偽装、fork/public/branch拒否、Check一意性・必須項目、ZIP禁止項目 | ローカルMock以外は信頼済みevent fileが必須で、正式ZIPだけが提出物として検証される |

## 共通完了条件

- 既存テストを維持し、第2回レビューの必須テストを追加する。
- format、Ruff、mypy、全pytest、coverage、`ai-dev validate`を合格させる。
- Secret/Data scan、依存関係監査、ビルド、正式ソースZIP検査を行う。
- 実Claude・実GitHub・実Actionsの未実施項目は「外部未受入」と記録する。
- main merge、本番操作、ロールバックを追加しない。
