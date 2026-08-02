# {{ project_name }}

このリポジトリは`ai-development-platform`で初期化された、人間承認を前提とするAI支援開発プロジェクトです。

Mockでの再現、実装済み、実Claude／実GitHubでの受入済みは別の状態です。通常テストは`provider: mock`と`github.gateway: mock`を使い、限定受入完了前にOperational MVP完成とは判断しません。

## 開始

```powershell
ai-dev doctor
ai-dev validate
ai-dev chat
ai-dev run --issue <number>
```

Linux/macOSでも同じ`ai-dev`コマンドを利用できます。

Issue Formの構造化要件と12件の`deployment_answers`は、対応する二つのダイジェスト付き人間承認コメントと`ai:approved`が揃うまで候補です。Issue変更でdigestが変われば再承認が必要です。

```powershell
ai-dev issue-approval-template --issue <number>
ai-dev issue-preflight --issue <number>
```

実Claude開発では`.ai-dev/project.yaml`の`github.enabled`と`github.gateway: gh`を設定し、`github.allowed_actors`へ手動実行を許可する人間を列挙します。mainの`AI開発オーケストレーター`へ承認済みIssue番号を入力すると、Claude開発、ホスト検証、Issueブランチへのcommit/push、PR作成まで進んで停止します。PR後のSystem Review、Business Review、QAは独立した`ai-quality-gates.yml`が担当します。mainへのマージ、本番デプロイ、本番データ変更はAIが自動実行しません。Secretと本番相当データをGitへ追加しないでください。

AI変更後のテスト、lint、型検査、依存監査、Secret scanは`.ai-dev/policies/verification.yaml`に従ってホスト側が実行します。pytest JUnitから取得した実行済みPASS test caseだけを受入条件の証拠にでき、Verification全体のPASSやAgentの自己申告だけでは要件充足にもcommitにも使いません。正式なPR品質ゲートは`.github/workflows/ai-quality-gates.yml`でCI→System→Business→QAの順に実行します。

正式提出物は次の手順で生成・検査したZIPだけです。

```powershell
ai-dev package-source --output {{ project_name }}-source.zip
ai-dev verify-source-package --archive {{ project_name }}-source.zip
```

cleanなGit commitからだけ生成でき、manifestのcommit SHA、全file hash、package digestを検証します。`.git`、過去のZIP、仮想環境、cache、build、coverage、egg-infoは新しいSource Packageへ含まれません。

このリポジトリはPrivate useを前提とし、ライセンスは未選択です。外部公開前に依存ライセンスと配布条件を再確認してください。
