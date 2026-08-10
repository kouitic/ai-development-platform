# Agent Provider連携IF仕様

## 1. 目的

`AgentProvider`のドメイン契約と、外部Provider固有の転送契約を分離する。Claude APIのJSON Schema制約が変わっても、stage別の型、既定値、制約、Traceabilityを弱めず、Providerアダプター内の変更に閉じ込める。

## 2. 共通ドメイン契約

共通IFは`AgentProvider.execute(AgentRequest) -> AgentResult`とする。

- `AgentRequest.output_schema`は、ホストが最終結果へ適用する正式なJSON Schemaである。
- `output_schema`を指定する場合、Draft 2020-12として有効でなければ外部APIを呼ばず、`REJECTED / invalid_output_schema`を返す。
- 成功時の`AgentResult.output`はJSON objectであり、正式Schemaによるホスト検証を通過済みでなければならない。
- Providerが返した構造化出力を、そのまま状態遷移や品質判定へ使用してはならない。
- `MockAgentProvider`は正式Schemaに対応するドメイン結果を直接返す。外部転送契約を模倣する必要はない。

## 3. Claude転送契約

Claude Agent SDKの`output_format`には、stage別の正式Schemaを直接渡さない。正式Schemaは任意項目数、union、文字列長、数値範囲、動的mapなど、Claude Structured Outputs側の制約を超える可能性があるためである。

SDKへ渡すSchemaは次の固定エンベロープとする。

```json
{
  "type": "object",
  "properties": {
    "result_json": {
      "type": "string",
      "description": "A serialized JSON object matching the host output contract supplied in the prompt."
    }
  },
  "required": ["result_json"],
  "additionalProperties": false
}
```

正常な転送結果の例を次に示す。`result_json`の値はMarkdownではなく、JSON objectを直列化したJSON文字列である。

```json
{
  "result_json": "{\"decision\":\"PASS\",\"summary\":\"検証完了\"}"
}
```

この転送Schemaは任意項目0、union 0、追加プロパティ禁止で固定する。正式Schemaはタスクコンテキストと分離した出力契約としてプロンプトへ含めるが、Claude APIの`output_format.schema`には使用しない。

## 4. 受信処理

Claude結果は次の順で処理する。

1. 固定エンベロープをホスト側でも検証する。
2. `result_json`をJSONとして復号する。
3. 復号結果がJSON objectであることを確認する。
4. 復号結果を`AgentRequest.output_schema`で再検証する。
5. stage別Pydanticモデルと業務検証を通過した後だけ、状態遷移へ使用する。

エンベロープ違反、JSON復号失敗、配列やscalar、正式Schema違反はすべて`REJECTED / invalid_structured_output`とする。SDKが`structured_output`を提供しない場合に限り、互換経路として本文中の単一JSON objectを読み取るが、正式Schemaのホスト検証は省略しない。

## 5. Provider障害との区別

エラー分類は次のとおりとする。

| 状態・コード | 意味 | 外部API呼出し |
|---|---|---|
| `REJECTED / invalid_output_schema` | ホストが構成した正式Schema自体が不正 | しない |
| `REJECTED / invalid_structured_output` | Claude結果を復号できない、または正式Schemaに不一致 | 実施済みの場合がある |
| `ERROR / provider_api_error_<status>` | Claude APIが要求をHTTPエラーとして拒否 | 実施済み |
| `TIMEOUT / provider_timeout` | 設定時間内に完了しない | 実施済み |

Providerの例外本文、応答本文、Secret、未マスク入力を`AgentResult.summary`や通常ログへ含めない。

## 6. 変更時の契約試験

Claude IFを変更する場合は、少なくとも次を自動試験する。

- 固定エンベロープが`result_json`だけを必須とし、追加プロパティを拒否する。
- Developer、System Review、Business Review、QAの複雑な正式Schemaを`output_format`へ直接渡さない。
- 有効な`result_json`を復号し、正式Schema一致時だけ成功する。
- 不正JSON、正式Schema不一致、不正な正式Schemaを区別して拒否する。
- SDKのtimeout、HTTP error、Secret非表示の既存契約を維持する。

実Claude受入では、API要求が受理されたことだけで合格にしない。復号後のstage別結果、ホスト検証、同一commit SHAの品質ゲートまで成功した証拠を残す。
