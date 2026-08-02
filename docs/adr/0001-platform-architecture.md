# ADR 0001: ローカルCLI中心の階層化モノリス

- 状態: 採用
- 日付: 2026-08-01

## 文脈

MVPはWindows 11の個人利用から始め、GitHubと複数AIを連携しながら、承認、Secret、データ境界を強制する必要がある。常駐サーバー、Web UI、分散構成は要求されていない。

## 決定

Python 3.12のローカルCLIを階層化モノリスとして実装する。Typer/Rich/Prompt Toolkitをinterface、ユースケースとLangGraphをapplication、状態・承認・判定をdomain、Claude/GitHub/YAML/SQLiteをinfrastructureへ分離する。外部接続はProtocolとMockを持つ。

状態遷移はLLM自由文から独立したGuardで強制し、SQLiteへ監査可能に保存する。mainマージと本番操作はMVPの実行APIから除外し、GitHub/CODEOWNERS/Environment/IAMを将来の外部強制層とする。

## 理由

- ローカル導入とWindows/Linux互換を保ちやすい。
- 外部資格情報なしにMockで全フローをテストできる。
- 安全判定をLLMやUIから分離できる。
- Phase 2の追加AI・環境とPhase 3のProvider/Lockを契約追加で拡張できる。

## 影響

- 単一プロセスのためMVPでは並列コード変更を扱わない。
- SQLiteはローカル単一利用に適するが、組織・複数ホスト利用ではStore差替えが必要になる。
- 実GitHub設定やクラウドIAMはCLIだけで保証できないため、doctorと外部管理設定の両方が必要となる。

## 却下した案

- Webサービス/マイクロサービス: MVPの運用負荷と攻撃面が増える。
- GitHub Actionsのみの実装: 対話とローカル状態、秘密情報境界を扱いにくい。
- LLM自由文による直接遷移: 監査性と安全性を満たさない。
