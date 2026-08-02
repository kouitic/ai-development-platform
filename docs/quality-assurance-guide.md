# 品質保証ガイド

## 責任

QA担当AIは品質基準への適合状況を証拠から評価します。最終品質保証と残存リスク受容は人間が行います。

## 必須入力

業務・システム要件、受入条件、期待結果、デプロイ先、環境、データ分類、リリース/停止/復旧/バックアップ/ロールバック方針、テスト結果、両レビュー、未解決バグ、要件トレーサビリティを用います。

## 判定

- `PASS`: 必須証拠が揃い、critical/major未解決や必須失敗がない。
- `PASS_WITH_CONDITIONS`: 基準は満たすが、明示条件と残存リスクがある。
- `REJECT`: 必須テスト/レビュー失敗、重大問題、要件不充足がある。
- `INSUFFICIENT_EVIDENCE`: テスト、追跡、レビュー等の証拠が不足する。

証拠不足をPASSにしてはいけません。QA PASSでも「定義された品質基準を満たしています」とだけ表現し、完全保証とは表現しません。

## 追跡

`RequirementsResult.requirements`のIDから設計文書、実装ファイル、各受入条件の実行済みtest case、要件種別ごとの必須Review、QA結果までを追跡します。初期Traceabilityには要件IDだけを登録し、Verification成功から参照を自動補完しません。

正式証拠は次をすべて満たす必要があります。

- 設計・実装パスがリポジトリ内にあり、保護パス規則を満たし、対象commitに存在する
- 受入条件文字列が承認済み要件と完全一致する
- test case IDがホスト生成JUnitに存在し、対象commitで実行済みかつ`PASS`である
- `SKIP`、`FAIL`、`ERROR`、別commit、未実行testを成功証拠にしない
- Review結果が評価対象要件IDを明示し、`review_coverage`で必要なSYSTEM・BUSINESS・QAを満たす

必須要件の欠落、未知・重複ID、設計/実装参照なし、受入条件ごとのPASS testなし、必須Reviewなしは`INSUFFICIENT_EVIDENCE`としてQA前に停止します。任意要件の必須化は`optional_requirement_traceability_required`設定に従います。

## 要件承認

構造化Issueは承認済みとはみなしません。現在の要件digestと一致する、人間の正式GitHubコメントがある場合だけ`RequirementsApproval`を成立させます。Issue本文変更、digest不一致、botコメント、後続の却下は承認として扱わず、再承認まで後続工程を停止します。

## Mock検証

外部APIなしで正常、テスト失敗、各レビュー不合格、QA不合格、3回上限、不正JSON、timeout、停止・再開、承認不一致、Secret/Data検出を再現します。実Claude/GitHubテストは任意・分離し、通常CIの合否に資格情報を要求しません。
