# 依存ライセンス記録

2026-08-02に`uv.lock`のruntime直接依存と`claude` optional依存について`pip-licenses`で確認した結果です。Claude SDKはMock-only installには含まれません。

| Package | Version | License |
|---|---:|---|
| jsonschema | 4.26.0 | MIT |
| langgraph | 1.2.10 | MIT |
| prompt_toolkit | 3.0.53 | BSD License |
| pydantic | 2.13.4 | MIT |
| PyYAML | 6.0.3 | MIT License |
| rich | 14.3.4 | MIT License |
| typer | 0.27.0 | MIT |

`claude` extraの直接依存:

| Package | Version | License |
|---|---:|---|
| claude-agent-sdk | 0.1.81 | MIT License |

本リポジトリ自体はPrivate useで、ライセンスは未選択です。外部公開または配布前に、runtime/dev/transitive依存のlock時点のライセンス、NOTICE要否、配布条件を再確認してください。

確認コマンド:

```powershell
uv sync --frozen --extra dev --extra claude
uv run --no-sync pip-licenses --format=markdown
```
