# AGENTS.md

## 目的

このリポジトリは、人間の最終判断を保ちながらAI開発チームの設計、実装、テスト、レビュー、QAを統制する汎用CLI基盤です。

## 最初に読む文書

`docs/requirements-analysis.md`、`docs/architecture.md`、`docs/mvp-scope.md`、`docs/security-model.md`、`docs/data-governance.md`、`docs/implementation-plan.md`の順に確認してください。

## 構成

- `src/ai_dev_platform/domain`: 外部技術に依存しないモデルと遷移
- `src/ai_dev_platform/application`: ユースケース、検証、LangGraph
- `src/ai_dev_platform/infrastructure`: SQLite、GitHub等のアダプター
- `src/ai_dev_platform/providers`: Agent Provider
- `src/ai_dev_platform/templates/project`: `init`生成物
- `.ai-dev`: このプロジェクト自身の設定
- `tests`: Mock中心の自動テスト

## コマンド

セットアップは`uv sync --extra dev`。開発中は`uv run ruff format .`、`uv run ruff check .`、`uv run mypy`、`uv run pytest`、`uv run ai-dev validate`を使用してください。

## 規約と変更範囲

Python 3.12、型ヒント、公開APIのdocstring、ドメイン/インフラ分離、構造化・マスク済み監査ログを維持してください。Issueの受入条件に必要な最小範囲だけを変更し、既存ファイルを無断上書きしないでください。

## 保護対象

`.github/**`、`.ai-dev/**`、`orchestrator/**`、`docs/business-rules/**`、`docs/review-standards/**`、`docs/quality/**`、`evaluation/expected/**`は人間承認なしに変更できません。レビュー通過目的でAgent定義、プロンプト、品質基準、期待結果を変更してはいけません。

## 作業開始前と完了時

開始前にIssue、現在状態、commit SHA、保護パス、データ分類、必要承認を確認してください。完了時に要件追跡、テスト、両レビュー、QA、Secret/Data scan、文書更新、残存リスクを確認してください。

## 禁止と人間承認

Secretと本番相当データをGit、会話、State、Artifact、ログへ保存してはいけません。mainへ直接pushしてはいけません。業務要件、業務ルール、品質基準、期待結果、Agent/CI定義、外部サービス、Secret/IAM/ネットワーク、本番相当データ、破壊的DB変更、mainマージ、本番操作、ロールバックは人間承認対象です。
