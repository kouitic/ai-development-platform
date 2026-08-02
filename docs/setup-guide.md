# セットアップガイド

## 1. 前提

Python 3.12または3.13（3.14未満）、uv、Gitを用意します。実GitHub連携には`gh`、実Claude連携には`claude` extraとClaude Agent SDKが利用する認証が必要です。資格情報とClaude SDKなしでもMockで全フローを試せます。

## 2. Windows PowerShell

```powershell
python --version
git --version
uv --version
uv sync --frozen --extra dev
uv run ai-dev --help
```

uvがない場合はAstralの公式手順または`python -m pip install uv`を利用できます。インストール方法は組織のソフトウェア導入ルールを優先してください。

## 3. Linux/macOS

```bash
python3 --version
git --version
uv --version
uv sync --frozen --extra dev
uv run ai-dev --help
```

## 4. 初期診断

```powershell
uv run ai-dev doctor
uv run ai-dev validate
```

`doctor`は資格情報の値を表示しません。Mock設定ではClaude SDK、Anthropic資格情報、GitHub CLIを`not required`として扱います。branch protection、Secret scanning、Push protection、GitHub Environments/IAMは外部設定のため、`not_checked`の場合はGitHub管理画面で確認します。

## 5. 新規プロジェクト

空の対象リポジトリで次を実行します。

```powershell
ai-dev init <project-name>
ai-dev doctor
ai-dev validate
```

既存ファイルと1件でも競合する場合、`init`は何も上書きせず停止します。差分を確認して人間が解決してください。

## 6. 任意の実接続

`.ai-dev/project.yaml`のProvider/Gatewayを変更する前に承認を得ます。Secret値は`.env.example`へ記載せず、OS環境変数、資格情報ストア、GitHub Secrets等へ設定します。

```powershell
$env:AI_DEV_PROVIDER = "claude"
$env:ANTHROPIC_API_KEY = "<OSまたはSecret管理から設定>"
uv sync --frozen --extra dev --extra claude
gh auth login
```

コマンド例に実値を貼り付けた履歴を共有しないでください。通常検証ではProviderとGitHub Gatewayを`mock`のままにします。

## 7. pre-commit

```powershell
uv run pre-commit install
uv run pre-commit run --all-files
```

gitleaks hookを利用する場合はgitleaks実行ファイルも別途必要です。CIでは専用ActionでSecret scanを実行します。
