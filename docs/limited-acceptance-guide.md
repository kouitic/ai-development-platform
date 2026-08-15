# 実Claude＋実GitHub限定受入ガイド

## 目的と判定

Operational MVPを完成と判断する前に、機密情報や本番相当データを使わないPrivate限定リポジトリで、実GitHubと実Claudeの境界を確認する。未実施、失敗、証拠なしは合格にしない。

## 事前承認

人間が対象repository、Issue、許可branch、予算、Claude利用、GitHub Actions変更、Secret/IAM、network、合成データ、停止条件を承認する。本番Secret、本番相当データ、本番権限は用意しない。

## GitHub設定

1. Privateの限定repositoryを作る。
2. mainへの直接pushとforce pushを禁止する。
3. CODEOWNERS、required review、`ai-quality/system-review`、`ai-quality/business-review`、`ai-quality/qa-assessment`、`ai-quality/final`を必須にする。
4. Secret scanningとPush protectionを有効化する。
5. 開発Workflowは`contents: write`、`issues: read`、`pull-requests: write`、レビューWorkflowは`contents: read`、`issues: read`、`pull-requests: write`、`checks: write`に限定する。
6. `ANTHROPIC_API_KEY`をGitHub Secretへ登録し、値をIssue、ログ、Artifactへ出さない。

## 合成受入ケース

Secretや個人情報を含まない小さな変更IssueをIssue Formで作り、構造化YAMLの要件ID、種別、説明、受入条件、必須性、対象範囲、対象外と、12件の`deployment_answers`を明記する。`issue-approval-template`が表示した要件・環境構成のコメントを人間が確認して投稿し、`ai:approved`を付与する。PR本文からIssueを`Closes #<number>`で関連付ける。最初のsystem reviewでmajor Findingが出る決定的な欠陥を含め、修正後にPASSできるケースを使う。

## 実行手順

1. `ai-dev doctor`と`ai-dev validate`を実行し、未確認の外部設定を人間が照合する。
2. `.ai-dev/project.yaml`で`github.enabled: true`、`github.gateway: gh`、実行を許可する人間の`github.allowed_actors`を設定する。
3. `ai-dev issue-approval-template --issue <番号>`の二つのコメントを人間が投稿し、`ai:approved`付与後に`issue-preflight`が成功することを確認する。Issue本文変更と後続の却下で失敗へ戻ることも確認する。
4. Actions画面からmainの`AI開発オーケストレーター`を手動実行する。安全判定は`workflow_dispatch` payloadのPrivate repository、入力Issue、sender、default branchと、`GITHUB_REF`、`GITHUB_WORKFLOW_REF`、`GITHUB_SHA`の一致から導出され、自己申告フラグでは起動できないことを確認する。
5. 許可外ファイル、`.env`読取り、shell、main push、force push、外部送信が拒否されることを確認する。拒否ログに入力値を残さない。
6. Claudeによる変更後にLocalVerificationRunnerがpytest、ruff、mypy、Secret scan、依存脆弱性検査、プロジェクト必須検査を実行し、PASS後だけcommit、push、PRがこの順で作成されることを履歴で確認する。開発WorkflowはPR作成後に停止し、System Review以降がPR Workflowだけで起動することも確認する。Verification run ID、worktree digest、基準SHA、commit SHAを記録する。
7. 通常CIのPython 3.12/3.13 matrixがMock-only frozen install、import、pytest、ruff、mypy、Source Package検査に成功し、Claude extraのimport確認にも成功することを確認する。
8. `github-actions-pinning.md`とWorkflowを照合し、全外部ActionがNode.js 24対応のレビュー済みfull commit SHAへ固定され、version tagが残っていないことを確認する。
9. system reviewへIssue本文、受入条件、PR diff、変更ファイル、検査集計が渡ることを、安全な参照IDで確認する。
10. Claudeの構造化出力が[Agent Provider連携IF仕様](provider-interface.md)の固定`result_json`エンベロープで受理され、復号後のstage別結果が正式Schemaのホスト検証を通過することを確認する。`provider_api_error_400`が発生した場合はAPI要求拒否として記録し、受信後の不一致は`invalid_structured_output_<detail>`で失敗境界を確認する。形式修復が発生した場合は、ツールなし・インターネットなし・1 turn・最大0.50 USD・1回だけであること、費用とturnが合算されること、候補本文がログとArtifactへ保存されないことを確認する。
11. major FindingでCheckがfailureとなり、Finding ID付きで実装へ戻ることを確認する。
12. 修正後の再レビューPASSまでFindingが未解決一覧から消えないことを確認する。
13. 統合`ai-quality-gates.yml`がSecret履歴検査後に4段階のProvider事前診断を行い、同一PR head SHAでSystem、Business、QAを一度ずつ順番に実行し、QAがtrusted verification、両レビュー、traceability、security scan、環境構成を統合することを確認する。事前診断を含む各JSON ArtifactとSHA-256も照合する。
14. QA条件付き合格が専用人間待ちとなり、最終承認を自動通過しないことを確認する。
15. GitHub comment失敗、Check失敗、Secret検出をそれぞれ模擬し、工程が進まないことを確認する。

## 保存できる証拠

Issue/PR URL、commit SHA、Check名と結論、GitHub comment/review ID、Artifact参照ID、テスト件数、coverage集計、Finding ID、実行日時、利用model、費用集計を保存できる。Prompt全文、Secret、環境変数一覧、本番相当データ、未マスクログは保存しない。

## 完了判定

`docs/mvp-completion-report.md`の13条件を1件ずつ証拠へ結び、失敗または未確認が0件で、人間が残存リスクを承認した場合だけOperational MVP completeへ変更する。main mergeや本番操作はこの受入の対象外であり、自動実行しない。
