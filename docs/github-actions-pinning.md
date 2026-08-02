# GitHub Actions外部Action固定台帳

## 目的

GitHub Actionsで実行する外部Actionを、Node.js 24対応のレビュー済みcommit SHAへ固定する。version tagの参照先変更や、未確認コードの自動取り込みを防止し、プラットフォーム用Workflowと生成テンプレートで同じ実体を使用する。

## 2026-08-02レビュー結果

| Action | 安定版 | 固定commit SHA | 実行環境 | 主な確認事項 |
|---|---|---|---|---|
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | Node.js 24 | `ref`と`fetch-depth`を維持。fork由来の危険なcheckoutに対する制御強化を確認 |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | Node.js 24 | `python-version`を維持。未使用の`pip-install`削除は影響なし |
| `astral-sh/setup-uv` | `v9.0.0` | `c771a70e6277c0a99b617c7a806ffedaca235ff9` | Node.js 24 | `enable-cache`と`prune-cache`を確認。キャッシュ費用増加を避けるため`prune-cache: true`を明示 |
| `gitleaks/gitleaks-action` | `v3.0.0` | `e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e` | Node.js 24 | v2から入力・出力・動作変更なし。runtimeだけNode.js 24へ移行 |
| `actions/upload-artifact` | `v7.0.1` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | Node.js 24 | `name`、`path`、`if-no-files-found`、`retention-days`を維持。新しい直接upload機能は不使用 |

各SHAについて、公式リポジトリ内の安定版releaseとの一致、`action.yml`のNode.js 24指定、使用中inputの存在、GitHubのcommit署名検証`verified: true / reason: valid`を確認した。

## 実行確認

commit `3555559`のGitHub Actions run [`30748749625`](https://github.com/kouitic/ai-development-platform/actions/runs/30748749625)で、Python 3.12・3.13の全工程が成功した。Node.js 20廃止予定警告は解消し、`setup-python`を`setup-uv`より先に実行してPython版ごとにキャッシュキーを分離したことで、キャッシュ予約競合の注記も解消した。

両jobでformat、lint、mypy、pytest、Secret scan、依存関係監査、Source Package生成・検証、Claude SDK importが成功し、Python 3.12 jobでは正式ZIP Artifactのuploadも成功した。

公式release：

- [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)
- [actions/setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0)
- [astral-sh/setup-uv v9.0.0](https://github.com/astral-sh/setup-uv/releases/tag/v9.0.0)
- [gitleaks/gitleaks-action v3.0.0](https://github.com/gitleaks/gitleaks-action/releases/tag/v3.0.0)
- [actions/upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1)

## 更新手順

1. 公式repositoryの安定版releaseと変更履歴を確認する。
2. `action.yml`のruntime、使用中input、権限・network上の影響を確認する。
3. release tagが指す40文字のcommit SHAと署名検証状態を確認する。
4. rootと`src/ai_dev_platform/templates/project`の全Workflowを同時に更新する。
5. version tagやbranch参照が残っていないことを自動テストで確認する。
6. Python 3.12・3.13 CI、Secret scan、Source Package、Artifact、Claude SDK importを再実行する。

自動的に`latest`を追従しない。更新は内容とSHAを人間が確認した独立変更として扱う。Self-hosted Runnerを将来利用する場合は、Node.js 24 Actionを実行できるGitHub Actions Runner `v2.327.1`以上を要求する。
