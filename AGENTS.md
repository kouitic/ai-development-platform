# AGENTS.md

## 適用範囲

この文書は、`ai-development-platform`自体を人間とCodexが保守・開発する際の規約（パターン①）です。

プラットフォームを利用して別のシステムを開発する際の規約（パターン②）は、`docs/repository-governance.md`、`docs/development-workflow.md`、`docs/github-configuration.md`および`src/ai_dev_platform/templates/project/AGENTS.md`で管理します。パターン②のmain直接push禁止、Issue必須、PR必須という制約を、このリポジトリ自体の保守作業へ混同して適用してはいけません。

## 作業開始の根拠

このリポジトリの開発では、人間によるチャット指示、添付指示または明示的な作業依頼を正式な作業開始の根拠とします。GitHub Issueの作成やIssue番号の指定は必須ではありません。

依頼された範囲だけを変更し、既存の未関連変更を保持してください。診断・説明だけを求められた場合は、明示されていない実装修正を行ってはいけません。

## ブランチ、コミット、PR

- 通常の作業ブランチは`main`です。別ブランチの作成は必須ではありません。
- PRの作成は必須ではありません。
- 人間が「コミット」「プッシュ」「mainへ反映」などを明示した場合は、検証済み変更をmainへ直接コミット・プッシュできます。
- 実装依頼はファイル変更を許可しますが、明示されていないコミットやプッシュまでは自動的に許可しません。
- 人間がブランチ名、PR作成または別のGit手順を指定した場合は、その指示を優先します。
- mainへ反映する前に、現在ブランチ、作業ツリー、remoteの差分、対象commit、テスト結果を確認してください。
- force push、履歴改変、未関連変更の取り消し、承認されていないブランチ削除は禁止します。

## 最初に読む文書

`docs/repository-governance.md`、`docs/requirements-analysis.md`、`docs/architecture.md`、`docs/mvp-scope.md`、`docs/security-model.md`、`docs/data-governance.md`、`docs/implementation-plan.md`の順に確認してください。

## 構成

- `src/ai_dev_platform/domain`: 外部技術に依存しないモデルと遷移
- `src/ai_dev_platform/application`: ユースケース、検証、LangGraph
- `src/ai_dev_platform/infrastructure`: SQLite、GitHub等のアダプター
- `src/ai_dev_platform/providers`: Agent Provider
- `src/ai_dev_platform/templates/project`: `init`生成物。パターン②の規約を含む
- `.ai-dev`: パターン②を自己検証するための、このプロジェクト用設定
- `tests`: Mock中心の自動テスト

## コマンドと品質

セットアップは`uv sync --extra dev`を使用します。変更内容に応じて`uv run ruff format .`、`uv run ruff check .`、`uv run mypy`、`uv run pytest`、`uv run ai-dev validate`を実行してください。

Python 3.12互換、型ヒント、公開APIのdocstring、ドメイン/インフラ分離、構造化・マスク済み監査ログを維持してください。完了時は要件追跡、関連テスト、Secret/Data scan、文書、残存リスクを変更範囲に応じて確認します。

## 保護対象

`.github/**`、`.ai-dev/**`、`orchestrator/**`、`docs/business-rules/**`、`docs/review-standards/**`、`docs/quality/**`、`evaluation/expected/**`は、人間が変更対象として明示した場合に限り変更できます。該当パスを具体的に含む作業依頼は、その依頼範囲に対する承認として扱います。

レビュー通過だけを目的としてAgent定義、プロンプト、品質基準または期待結果を緩和してはいけません。パターン②の安全制御を変更する場合は、依頼内容と影響を明示して検証してください。

## 言語

このリポジトリに作成するIssue、PR、コミット、レビュー記録および運用文書は日本語を基本とします。コード識別子、外部仕様の正式名称およびコマンドは原文を使用できます。

## 禁止と人間承認

Secretと本番相当データをGit、会話、State、Artifact、ログへ保存してはいけません。外部サービス、Secret/IAM/ネットワーク、本番相当データ、破壊的DB変更、本番操作、ロールバックなど、リポジトリ外へ重大な影響を与える操作は人間の明示承認を必要とします。
