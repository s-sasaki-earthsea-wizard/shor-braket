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

`make emulate-n15` は `raw/n15-emulation-<timestamp>/` に N=15 の QPU 互換回路（swap network, t=2）を
3 機 × 2 oracle で走らせた `result.json`（配置、ゲート数、厳密なノイズ付き分布と標本、信号残存率）、
`circuits/*.qasm`（verbatim 回路）、`figures/`、`report.md` を書く。`execution.class = local-emulator-n15`、
`qpu_gate.qpu_eligible = false`。

`make submit-qpu` は投入したタスクごとに `raw/qpu-<device>-<oracle>-<timestamp>-<hash>/submission.json` を
書く。validated レコードが「この回路は検証を通った」の証明なのに対し、これは「この課金はその証明に基づく」の
台帳で、タスク ARN、回路ハッシュ、参照した validated レコード、タグ、コスト概算、**投入時点の Spending Limit**、
呼び出し元の STS 識別子、結果の S3 位置、preflight レポート全文を含む。`raw/` は gitignore 対象なので、
追跡ファイルには書けないアカウント ID とプリンシパル ARN をここには残せる。

`make task-status` はこの台帳を読み、各タスクの現在の状態を `GetQuantumTask`（無料・読み取りのみ）で引く。
