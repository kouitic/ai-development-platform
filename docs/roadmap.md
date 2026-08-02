# ロードマップ

## 現在地（実装レビュー対応）

Phase 1を「foundation」と「operational acceptance」に分割する。foundationは完成済みである。現在はOperational MVPのGateway、証拠、Context、Git、Actions、環境対話、Runtime Policy、配布機能を実装・検証している。全ローカル品質ゲートが通っても、実Claude＋実GitHub＋Actionsの限定受入が終わるまではPhase 1完了としない。

次の順序は、(1) ローカルMock E2E、(2) Private限定リポジトリのGitHub/Actions受入、(3) 制限付きClaude受入、(4) 残存リスクへの人間判断、(5) Operational MVP判定である。Phase 2のリリース・運用自動化へはこの判定後に進む。

## Phase 0: 分析・設計

- 目的: 安全境界、MVP範囲、拡張点を実装前に合意可能な形にする。
- 対象機能: 要件分析、アーキテクチャ、ADR、セキュリティ、データ統治、Issue分割。
- 対象外: 実行コード、外部接続。
- 前提: 元指示を正とし、不明点は安全側の既定値を採る。
- 完了条件: 指定事前文書が揃い、重大な矛盾がない。
- 次Phaseへの依存: 設定モデル、状態、承認境界が明確であること。
- 将来拡張ポイント: Provider、Agent、Workflow、Store、Gateway。

## Phase 1: MVP

- 目的: 単一リポジトリのAI開発フローをMockで再現し、安全に実接続へ切替可能にする。
- 対象機能: CLI、5 AI、LangGraph、SQLite、GitHub抽象、Claude抽象、3回反復、承認、scanner、テンプレート、CI、文書。
- 対象外: Web UI、並列コード変更、本番自動化、実データ検証、追加AIの完全実装。
- 前提: Python 3.12、uv、Git。実接続には別途Claude/GitHub認証が必要。
- 完了条件: MVP完了条件をMock E2Eと文書で追跡できる。
- 次Phaseへの依存: review typeとenvironment quality gateの拡張APIが安定していること。
- 将来拡張ポイント: Agent registry、environment、quality evidence、restricted job。

## Phase 2: レビュー・環境・運用拡張

- 目的: UI品質、運用設計、複数環境、本番前検証を安全に追加する。
- 対象機能: デザインレビューAI、リリース・運用設計AI、Storybook、アクセシビリティ、複数環境、GitHub Environments、リリース/DB移行、本番相当データ専用検証、品質分析、クラウドGateway。
- 対象外: 複数コード変更の同時実行、実本番の完全自動化、組織横断管理。
- 前提: MVPの承認、監査、分類、Provider契約が運用実績を得ている。
- 完了条件: 環境ごとの品質ゲートと人間承認を越えないリリース計画が検証される。
- 次Phaseへの依存: Lock、versioned requirement、組織ポリシーの設計。
- 将来拡張ポイント: deployment strategy、quality analytics、restricted runner。

## Phase 3: 規模・高度自動化

- 目的: 競合とガバナンスを制御しながら規模を広げる。
- 対象機能: 並列タスク、競合検出、排他Lock、要件差込み、複数Provider/Repo、組織ポリシー、長期分析、コスト最適化、段階リリース。
- 対象外: 人間最終責任の代替、承認不要の重大操作。
- 前提: Phase 2までの監査データと運用基準がある。
- 完了条件: 故障・競合・権限逸脱時に安全停止し、全操作を監査できる。
- 次Phaseへの依存: 未定。
- 将来拡張ポイント: 新しいProvider、リリース方式、組織ポリシーパック。
