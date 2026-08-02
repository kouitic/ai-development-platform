# 将来アーキテクチャ

## Phase 2

MVPの`AgentDefinition`とレビュー共通結果に`design_review`、`release_operations_review`を登録する。ワークフロー本体へ条件分岐を直書きせず、プロジェクトの必須レビュー一覧とquality gateからノードを組み立てる。

環境は`EnvironmentDefinition`として目的、データ分類、Secret scope、必須レビュー、昇格条件を持つ。GitHub Environmentsは外部強制レイヤーとして利用し、ローカル承認だけで本番へ進めない。制限付きデータ検証は通常Runner/State/Artifactから完全に分離した`RestrictedValidationGateway`へ委譲する。

## Phase 3

単一タスク制御を`LockManager`へ置き換え、リポジトリ、ブランチ、保護パス単位のLeaseを扱う。要件はversionとcommit SHAを持ち、途中変更は新versionの影響分析後に安全な再開点へ適用する。

複数Providerは能力、データ境界、コスト、リージョン、可用性を条件とするroutingを採用する。組織ポリシーは署名付き読み取り専用bundleとし、プロジェクト設定で緩和できない。

## 互換性原則

- 状態と監査イベントはversion付きSchemaで保存する。
- 新しい状態・レビューは未知値として安全停止できる。
- Provider、Gateway、StoreはProtocol契約を維持する。
- データ分類と承認要件は追加方向のみを既定とし、暗黙の緩和をしない。
- migrationsは前方移行とバックアップ手順を持つ。
