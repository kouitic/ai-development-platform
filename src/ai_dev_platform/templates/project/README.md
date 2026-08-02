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

Issue Formの構造化YAMLは正式要件です。自然言語からAIが生成した候補は`REQUIREMENTS_APPROVAL_REQUIRED`で停止し、`approve --stage requirements`による人間承認後だけ利用します。その後、デプロイ・環境構成の人間回答待ちで停止します。mainへのマージ、本番デプロイ、本番データ変更はAIが自動実行しません。Secretと本番相当データをGitへ追加しないでください。

AI変更後のテスト、lint、型検査、依存監査、Secret scanは`.ai-dev/policies/verification.yaml`に従ってホスト側が実行します。Agentの自己申告結果だけではcommitしません。正式なPR品質ゲートは`.github/workflows/ai-quality-gates.yml`でCI→System→Business→QAの順に実行します。

正式提出物は次の手順で生成・検査したZIPだけです。

```powershell
ai-dev package-source --output {{ project_name }}-source.zip
ai-dev verify-source-package --archive {{ project_name }}-source.zip
```

過去のZIP、仮想環境、cache、build、coverage、egg-infoは新しいSource Packageへ含まれません。

このリポジトリはPrivate useを前提とし、ライセンスは未選択です。外部公開前に依存ライセンスと配布条件を再確認してください。
