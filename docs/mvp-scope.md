# MVP範囲

## 現在の境界

- `MVP foundation complete`: 設定、CLI骨格、Mock Agent/GitHub、SQLite、決定的状態遷移、初期化・検証、方針・テンプレート、Mock試験。
- `Operational MVP incomplete`: 実Claude、実GitHub、実Actions、実サンドボックスを使う限定受入が未完了のため、運用完成とは判定しない。

Operational MVPには、構造化Issueの正式要件、AI要件候補の人間承認、要件ID・受入条件単位のTraceability、stage別Context、AI変更後のホストVerification、検証後だけのcommit/push/PR、PR差分レビュー、origin別Finding lifecycle、同一SHAの統合Actions品質ゲート、QA証拠統合、デプロイ対話、GitHub payload由来の実行時Policy、Python 3.12/3.13 clean CI、限定受入手順を含む。merge、本番デプロイ、本番データ変更、rollback APIは引き続き対象外とする。

## 目的

外部AIやGitHubの実資格情報がない環境でも、安全な開発ワークフロー全体をMockで実証し、資格情報を設定すれば抽象化された接続先へ切り替えられるローカルCLIを提供する。

## 完了に含むもの

- 初期生成、設定検証、環境診断
- 自然言語のIssue案、実GitHubまたはMockへのIssue作成
- 5つのAI役割と可変な業務レビュー定義
- 構造化された状態遷移、レビュー、QA判定
- 3回までの修正反復とBLOCKED
- SQLite保存、resume、工程境界pause/cancel
- commit SHAに結び付いた人間承認待ち
- Secretと本番相当データの混入防止
- GitHub Flowブランチ名、Issue/PR/Actionsテンプレート
- 自動テスト、セットアップ、開発・運用ガイド

## 意図的な制限

- コード変更タスクは1リポジトリ1件。
- 実行中の要件変更は取り込まず、新Issueにする。
- mainマージ、本番デプロイ、本番データ操作を行うAPIは提供しない。
- Claude/GitHub実接続は明示選択時のみで、通常テストはMockを用いる。
- 本番相当データ実体は扱わず、メタデータと集計証拠だけを扱う。

## MVP完了判定

`docs/requirements-analysis.md`のOPM-001〜015を、コード実装、Mock検証、実外部受入の3段階に分けて追跡する。外部管理者設定が必要なブランチ保護、Secret scanning、Push protectionは`doctor`で状態を示し、ローカル実装の完了と外部設定済みを区別する。

## 品質表現

QAがPASSを返した場合も「定義された品質基準を満たしています」と表現する。最終的な品質保証、残存リスク受容、mainマージ、本番操作は人間が行う。
