# 第3回レビュー指摘の事前分析

## 実施条件

作業場所には`.git`がないため、commit SHA、追跡状態、実PRは取得できない。`.github/**`と`.ai-dev/**`は今回の明示指示に必要な最小範囲だけ変更する。実GitHub Actions、Python 3.13、クリーンな外部Runnerで未確認の項目は外部未受入として残す。

## 指摘別分析

| 区分 | 原因 | 影響 | 修正方針 | 主な対象 | テスト・完了条件 |
|---|---|---|---|---|---|
| System Review登録 | 現コードのappendは1回だが、`run_id`を一意キーとする保存制約がなく、旧状態や再実行の重複を排除できない | 同一レビューがBusiness/QAの入力へ重複し、件数と証拠を誤認する | 全stage結果を`run_id`で冪等保存し、QAコンテキストでも既存重複を除去する | `workflow_runner.py`、`context_builder.py` | 1回で1件、同一`run_id`再保存で増加なし、QA入力重複なし |
| 実要件Traceability | 品質ゲートが固定`ISSUE-REQUIREMENTS`を生成し、QA前提がtraceability非空だけを確認している | 個別要件、受入条件、実装、テスト、レビューの対応を証明できない | `RequirementItem`、構造化Issue parser、受入条件別テスト参照、完全性validatorを導入する | domain、quality gate、workflow、Issue Form、Schema | 必須要件欠落、未知ID、重複ID、受入条件テスト不足を拒否し、完全時だけQAへ進む |
| 要件承認 | AI要件分析結果がそのまま正式要件として後続へ進む | 人間未承認の候補が設計・品質基準になる | AI候補は`REQUIREMENTS_APPROVAL_REQUIRED`で停止し、commit-boundな正式承認後だけ次工程へ進める。人間が登録した構造化Issueは正式入力として扱う | state、approval service、CLI契約、文書 | 未承認候補で停止、承認後遷移、構造化Issueのみ品質ゲート開始可能 |
| 依存分離 | Claude SDKが基盤必須依存に含まれる | Mock専用のinit/validate/test/packageでもSDK解決が必要になる | `claude` extraへ移し、Claude選択時だけSDK存在を明示検査する | `pyproject.toml`、`uv.lock`、provider factory、CI | Mock extraにSDKなし、Mock処理成功、Claude選択時不足理由が明確 |
| Python/clean install | 対応上限と3.13検証が未定義 | 未検証Pythonでの利用可否が曖昧 | `>=3.12,<3.14`へ固定し、3.12/3.13 matrixでfrozen Mock install、全品質検査、Claude extra importを実行する | pyproject、lock、root/template CI | YAML構文とmatrix内容を検証。実Actions未実施は外部未受入 |
| Source Package | 明示的な`.zip`除外がなく、許可ディレクトリ内の過去ZIPを取り込み得る | 提出物の入れ子、不要・未知データ混入 | あらゆるZIPを除外・検証時拒否し、同じrootで反復生成しても入れ子にならないようにする | package service、CI、README | venv/cache/build/coverage/egg-info/過去ZIP 0件、正式Artifact生成 |
| Secret scan | tree scanの除外が不完全で、Local Verificationが全treeを毎回走査する | cache/build誤検出と性能低下。一方、除外配下の追跡変更を見逃す設計にもなり得る | tree scanは生成物を除外し、Local Verificationは変更ファイルと必須設定を明示パスで検査する。CIの履歴・追跡全体はgitleaksへ分離する | scanner、verification、CI | 除外dir/ZIP非走査、明示追跡ファイルのSecret検出、ZIP非展開 |
| Action固定 | 開発用Major tagとOperational固定条件の境界が未文書化 | Operational時にサプライチェーン更新を暗黙受入する | 開発中はMajor tag、Operational受入前にfull commit SHA固定、Dependabotまたは明示PR更新を必須化する | `security-model.md` | 方針と未受入条件が明記される |

## 共通完了条件

- format、Ruff、strict mypy、全pytest、coverage、`ai-dev validate`を通す。
- Mock環境、Claude extra、Python 3.12/3.13のうち、この環境で実行していないものを完了扱いにしない。
- Secret/Data scan、依存監査、build、正式Source ZIP自己検証を行う。
- キャッシュ、build、過去ZIPを正式提出物へ含めない。
