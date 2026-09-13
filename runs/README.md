# Local simulator and Braket run artifacts

- `validated/` — 検証済みレコード。git 管理対象（監査性のため）
- `raw/` — ローカル実行と将来の Braket 実行結果。gitignore 対象

`make sim` は `raw/local-n15-a7-<timestamp>-<hash>/result.json` を作る。レポートには
回路ハッシュ、解析時の同時分布、サンプリング結果、復元した位数と因数、依存バージョン、
将来の QPU 候補 3 機の費用概算を含む。

行列参照回路のレポートは `execution.class = local-reference`、
`circuit.qpu_eligible = false` となる。このレポートを QPU 投入許可には使用しない。

`make emulate DEVICE=<garnet|emerald|ibex>` は `raw/emulator-<device>-<timestamp>-<hash>/result.json` を
作る。`execution.class = local-emulator` で、verbatim 検証の結果、校正ノイズ付きの測定分布、深さスイープ、
参照したスナップショットの `capabilities_sha256` を含む。`make emulate-all` は加えて
`raw/emulator-comparison-<timestamp>/` に 3 機の比較図と `report.md` を書く。どちらも validated レコードを
発行しない。
