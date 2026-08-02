# AGENTS.md

## 適用範囲

この文書は、`ai-development-platform`を利用して対象システムを開発する際の規約（パターン②）です。プラットフォーム自体の保守規約ではありません。

## 目的

このリポジトリは、人間の業務判断と明示承認を保ったままAI開発チームを利用するシステム開発プロジェクトです。

## 最初に読む文書

`README.md`、`docs/requirements/`、`docs/architecture/`、`docs/business-rules/`、`docs/quality/`の順に確認してください。

## 構成とコマンド

設定は`.ai-dev/`、正式な開発記録はGitHub Issue/PR、評価ケースは`evaluation/`にあります。`ai-dev validate`、`ai-dev doctor`、`pytest`を作業前後に実行してください。

## 作業トリガーとブランチ

AIは、人間が承認した正式Issueをトリガーとして、Issueに対応する`ai/issue-<number>-<description>`形式の作業ブランチだけを変更します。設計開発AIはmainの変更、直接push、force push、PRの自己承認・自己マージを実行できません。

変更後はホスト側Verificationに合格した証拠をcommit SHAへ関連付け、作業ブランチからPRを作成します。System Review、Business Review、QAの必須評価後に、人間が証拠と残存リスクを確認してmainへのマージを承認します。AI自身はmainをマージしません。

## 規約と保護

型ヒント、公開APIのdocstring、ドメイン/インフラ分離、構造化ログを維持してください。`.github/**`、`.ai-dev/**`、`orchestrator/**`、`docs/business-rules/**`、`docs/review-standards/**`、`docs/quality/**`、`evaluation/expected/**`は保護対象です。AIは承認済みIssueの範囲内だけを変更できます。

## 禁止と承認

Secretと本番相当データをGit、会話、状態、Artifact、ログへ保存してはいけません。mainへ直接pushしてはいけません。業務要件、品質基準、期待結果、Agent/CI定義、外部サービス、本番相当データ、mainマージ、本番操作、ロールバックは人間承認対象です。
