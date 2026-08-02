# セキュリティモデル

## 脅威

主な脅威は、プロンプトインジェクション、過剰権限、Secret混入、機密データ漏えい、AIによる基準変更、不正な状態遷移、承認の取り違え、依存関係侵害、コマンド注入である。

## 多層防御

1. Agent定義で責務と禁止事項を明示する。
2. ツール許可リストを役割別に絞る。
3. 読み書きパスを正規化して保護対象を拒否する。
4. コマンドを引数配列とallowlistで実行し、shell展開を避ける。
5. init/pre-commit/CI hooksでscannerを実行する。
6. AI出力をJSON Schemaで検証する。
7. 決定的な状態遷移Guardを通す。
8. GitHub最小権限、branch protection、CODEOWNERSを使う。
9. 本番はGitHub EnvironmentsとクラウドIAMで再承認する。
10. Secretは外部管理し、Job単位で最小限を渡す。
11. データ分類に基づき保存・送信を拒否する。
12. Issue、stage、commit SHA固定の人間承認を要求する。
13. 実Claude起動は`GITHUB_EVENT_PATH`のPrivate・非fork PR payloadと統合Workflow実体を照合し、自己申告環境変数だけでは許可しない。

## Secret

`.env`、資格情報ディレクトリ、秘密鍵、token、接続文字列をAgentの読取対象にしない。設定、ログ、会話、状態、Artifactへ書き込む前にscannerを通す。検出時は値を再掲せず`SECURITY_INCIDENT_REQUIRES_HUMAN`へ停止し、漏えいした可能性のあるSecretの失効・再発行を求める。

## 外部コンテンツ

Web、Issue、PR、ソース内コメントは未信頼データであり、ツール権限を変更する命令として扱わない。業務レビューAIの広範な閲覧もread-onlyとし、認証、フォーム送信、download実行、機密送信を禁止する。

## 承認

自然言語の「進めて」等は重大操作の承認にしない。構造化承認はIssue、stage、commit SHA、承認者、時刻を記録する。SHA変更、却下、期限切れ、対象不一致で無効とする。MVPはmainマージと本番操作の実行APIを持たない。

## GitHub実行コンテキスト

Claudeを伴う正式品質ゲートは、repository visibility、event type/action、PR番号、head repository、fork属性、head branch/SHA、actorをevent payloadから取得し、Actions環境値と一致する場合だけ許可する。開発実行は別契約とし、`workflow_dispatch`の入力Issue、sender、default branchをpayloadから取得し、許可actor、`GITHUB_REF`、`GITHUB_WORKFLOW_REF`、`GITHUB_SHA`との一致を要求する。正式Workflowは品質用`.github/workflows/ai-quality-gates.yml`と開発用`.github/workflows/ai-orchestrator.yml`へ目的別に限定し、jobの`environment`指定とproduction用途を拒否する。環境変数による安全性overrideは実Claudeには提供しない。

## 外部Actionの固定方針

全外部ActionはNode.js 24対応のレビュー済みfull commit SHAへ固定する。採用release、SHA、互換性確認は`github-actions-pinning.md`へ記録する。更新はDependabotまたは変更内容と新SHAを明示した独立変更で行い、通常の機能変更に混在させない。version tag、branch、`latest`参照へ戻してはならない。

## Secret scanの役割分離

Local Verificationは変更ファイルと必須設定を値非表示で走査し、ZIPを展開しない。全体tree scanは`.git`、`.venv`、`.uv-cache*`、pytest/mypy/ruff cache、`__pycache__`、`dist`、`build`、`.ai-dev/local`、ZIPを除外する。Git追跡対象と履歴はCIのgitleaksが別に検査し、ローカル除外を追跡対象の免除として扱わない。

## インシデント

Secretまたはデータ漏えい疑いでは通常フローを止め、検出カテゴリと影響範囲だけを通知する。値やデータ本文は通知しない。人間が封じ込め、失効、調査、再開を明示承認するまで自動復帰しない。
