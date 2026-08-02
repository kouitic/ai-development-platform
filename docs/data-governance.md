# データガバナンス

## 分類

| 分類 | Git/通常CI | AI会話・SQLite | 主な用途 |
|---|---|---|---|
| PUBLIC_DUMMY | 可 | 可（必要最小限） | unit test、sample |
| SYNTHETIC | 機密を含まない場合のみ可 | 可（必要最小限） | test、evaluation |
| MASKED_PRODUCTION_LIKE | 不可 | 不可 | 専用検証環境 |
| RESTRICTED_PRODUCTION_LIKE | 不可 | 不可 | 人間承認済み専用検証 |
| PRODUCTION | 不可 | 不可 | 通常AI開発では使用しない |

匿名化済みデータも再識別リスクがあるため、本番相当として扱う。

## 役割別アクセス

- conversation/developer: 本番相当データへアクセス不可。
- system-reviewer: schema、集計値、検査結果のみ。
- business-reviewer: 人間承認済み専用環境で、限定項目・期間・read-onlyの場合だけ可。
- qa: 原則集計証拠のみ。生データは別承認と専用環境を要求する。

## 保存禁止先

Git、Issue、PR、コメント、通常Artifact/Actionsログ、AI会話、LangGraph State、通常SQLite、通常レポート、スクリーンショットへ本番相当データを保存しない。

品質ゲートの一時Artifactには、対象Issue番号、PR番号、commit SHA、review run ID、decision、Finding ID、Evidence参照、要約だけを保存する。Issue本文、PR diff、コマンド出力、Secret、本番相当データは含めず、JSONと隣接SHA-256を受渡し時に検証する。

Source Packageは許可されたsource/config/template/test/docsだけを含み、過去ZIPを含む全ZIPを入れ子にしない。Local Verificationは未知ZIPを展開せず、Git履歴・追跡対象の検査はCIのgitleaksへ分離する。

## 制限付き検証

Phase 2では、人間承認、commit SHA固定、専用Runner、read-only、ログ抑制、Artifact無効化、一時データ削除、監査、保存期限、目的記録を必須とする。MVPはデータ実体を扱わず、設定と拒否制御だけを実装する。

## 漏えい時

漏えい可能性を検出したら`DATA_EXPOSURE_REQUIRES_HUMAN`へ停止する。通知には分類、経路、影響範囲、推奨対応を記載し、データ本文を含めない。
