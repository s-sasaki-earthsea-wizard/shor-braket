# CLAUDE.md — shor-braket

Shor のアルゴリズムの位数発見回路を実装し、ローカルシミュレータ検証を通ったものだけを
Amazon Braket で実行するプロジェクト。検証の主題は N = 15、当初要件の N = 6 は縮退ケースとして併設。
AWS リソースは Terraform 管理。

---

## 絶対に忘れてはいけない前提

### 0. 優先順位: まずローカル、AWS は後

**Phase 1–2（ローカルシミュレータ）が最優先。** AWS 側（Phase 3 以降）は設計だけ先に
固めてあり、着手はローカルが通ってから。AWS の作業を先回りして進めないこと。

**2026-09-13: AWS 側の構築を中断した。** IAM（3 プリンシパル + 実行ロール + ガードレール）までは
Terraform で構築済みで、root アクセスキーも廃止済み。残りは GitHub issue #1–#5 に記録してある。
**実機実行が現実味を帯びるまで再開しない。** 再開の入口は issue #1。

issue #6 は完了。issue #7 の主要項目も 2026-09-14 に決着した。**issue #8（validated レコードと投入ゲート）も
2026-09-14 に実装完了**（PR #16。マージされたら #8 を閉じる）。実タスク投入は **issue #17** に分離した。
**2026-09-15: タグ集合と SV1 の要否も決着した**（ADR-0004）。残る未決はレコードの有効期限の日数、
`--yes` の運用、IBEX の実行ウィンドウ運用。

**2026-09-15: AWS 側を再開し、ADR-0004 の IAM を apply した。** IAM は 24 リソースで差分ゼロ
（ユーザー 2・ロール 2・ポリシー 6・アタッチ 13 + caller identity）。**`make iam-verify` 14/14 が期待どおり**で、
結果は `infra/iam/README.md` §7 に記録済み（issue #2 は完了）。旧 `shor-braket-assume-exec` は消え、孤児なし。
請求情報への IAM アクセスは有効化済みを確認した。**残りは #1 → #3 → #4 → #17。**
順番は **手順 0 ローカル準備（済） → 1 IAM の plan / apply（済） → 2 `iam-verify`（済） →
3 operator の MFA と exec / aqt プロファイル（#1、済）→ 4–5 Phase 3 の Terraform（#3、済）→
6 コスト配分タグの有効化（#4、キー出現まで約 24 時間）→ 7 Garnet 10 ショットの経路確認（#17）**。
**残るは #4 → #17。**
**2026-09-16: #1 と #3 を片付けた。** operator の MFA を登録し、exec / aqt の両プロファイルで
assumed-role を確認（#1 クローズ）。Phase 3 の Terraform（`s3.tf` / `budget.tf` / `spending_limits.tf` /
`cost_allocation_tags.tf`、awscc provider、IAM の `SearchSpendingLimits` 許可と Create / Update / Delete の Deny）を
書き、**Syota さんが apply した**（11 作成 / 3 in-place、destroy なし）。結果バケットは
`amazon-braket-shor-braket-earthsea-wizard`（`amazon-braket-` は Braket のサービスリンクロールが書ける範囲なので必須）。
apply 後の実測で API の挙動が 2 つ分かった。**`spending_limit` は最短表記に正規化される**ので
`format("%.2f", …)` の `"0.00"` は毎回 plan に差分を作る（`tostring()` に変更して解消）。
**`time_period` を省略しても API が作成時刻〜2125 年の期間を付ける**（awscc は computed として受けるので差分なし）。
Budget のタグフィルタはタグ有効化前でも作れた。
決定: CloudWatch ロググループは作らない、S3 の Glacier 移行は無し、Spending Limit の期間は
初回未設定で実験ごとに設定、Budget 通知は SNS + email、`.terraform.lock.hcl` はコミット、stage 2 の Garnet は 5 USD。
`gate/spending.py` が実 API と違うフィールド名（`currentSpend` / `timePeriod.start`）を読んでいたバグは PR #21 で修正。
**`results_bucket_name` は `amazon-braket-` プレフィクスが必須**（validation）。
**Terraform は admin で回す。plan は Claude が回してよく、apply / destroy は Syota さん本人が実行する。**
admin を使うのは Terraform と鍵・MFA の発行だけで、日常のコマンドはプロジェクトの IAM（ro / exec / aqt / monitor）で回す。
Budget はコスト配分タグ `project` でフィルタし、月次累計は Budgets の `CalculatedSpend` から取る（Cost Explorer は使わない）。
コスト配分タグは `aws_ce_cost_allocation_tag` で Terraform 化する。有効化後のデータにしか効かないので、
**実機の本実験は有効化から 24 時間後以降**。有効化するタグは `project` / `oracle` / `campaign` の 3 つ。
Braket の第三者デバイス規約への同意だけは CLI に無く、経路確認が規約で落ちたときに限りコンソールを使う。

**2026-09-14: LocalEmulator 互換性スパイク完了。** 3 機（IQM Garnet / Emerald、AQT IBEX Q1）の校正データを
`devices/snapshots/` にコミットした。`make device-snapshot` は読み取りプロファイルで `GetDevice` を呼ぶだけで
課金も Terraform も不要。`make emulate` / `emulate-all` はオフラインで verbatim 検証と校正ノイズ付き実行を行う。
**2026-09-14: N=15 の QPU 互換回路を実装し 3 機でエミュレーション済み**（`quantum/n15.py`、`quantum/routing.py`、
`quantum/compile.py`、`runner/n15.py`、`make emulate-n15`）。swap network の U_7 / U_4（`generic-constant`、t=2、6 qubit、
論理 2q ゲート 46）は「15 = 2^4 − 1 を使う N=15 専用の乗算分解」であり、t=2 は r ≤ 4 の知識を使う。結果は
**因数分解の成功と書かない**。「手掛かりの下で周期 4 の信号がどれだけ残るか」の観察として記録する
（信号残存率 Emerald 0.73 / Garnet 0.54 / IBEX 0.39。Wiki「N=15 を QPU 互換回路で」）。
**2026-09-14: 反復 QPE（feed-forward）を実装し Garnet / Emerald でエミュレーション済み**（`quantum/feedforward.py`、
`quantum/n15_iterative.py`、`runner/n15_iterative.py`、`make emulate-n15-iterative`、issue #7）。count qubit 1 個を
`measure_ff` → `cc_prx` の能動リセットで再利用し、位相補正は `cc_prx` 対で入れ、中間測定の結果は記録用 qubit にコピーする
（実機は MCM の結果を返さない）。**結果: t=2 では λ は標準 QPE と ±0.03 以内で差が無い**（Emerald constant 0.70 vs 0.73、
Garnet constant 0.53 vs 0.54）。SWAP は Fredkin の三角形要求で決まり count register の数に依存しないため。
TVD の標本床（理想分布 1,000 shots で 0.049、20,000 で 0.011、近似式 Σ√(p(1−p)/(2πn))）とサポート質量由来の λ（不偏）も
数値化した。**issue #7 の主要な未決事項（反復 QPE・深さ予算・TVD 閾値・実機の合否・DM1）は決着済み**で、下の「決定事項」表と
`analysis/distribution.py` の `noiseless_verdict` / `noisy_verdict` が最終形。残る未決はタグ集合、validated レコードの有効期限、
`--yes` の運用、月次累計の取得元、IBEX の実行ウィンドウ運用。**次は issue #8 の validated レコードと投入ゲート。**
SDK のバグ 2 件は issue #14 / #15 に最小再現つきで記録済み（upstream 報告は本題の実験の後）。

**2026-09-14: validated レコードと投入ゲートを実装した**（`gate/circuit_hash.py`、`gate/record.py`、
`gate/preflight.py`、`gate/spending.py`、`runner/submit.py`、`make circuit` / `validate-n15` / `validated` /
`submit-qpu`、issue #8）。回路ハッシュは OpenQASM テキストではなく**正規化した IR**（命令列の辞書を JSON 化）
に対して取る。**target の順序は保存する**（`sorted(targets)` だと `cnot(0,1)` と `cnot(1,0)` が衝突する。
docs/03 §3.2 の初稿を訂正した）。レコードの失効判定で実質の主判定になるのは日数ではなく
`capabilities_sha256` の一致。**投入ゲートは回路側の検査を全部通り、止めていたのは Spending Limit だけ**だった。2026-09-16 に #3 が
それを作ったので、ゲートを閉じているのは Limit が 0 USD であることと `submit()` が常に
`NotImplementedError` を投げること（#17）になった。
**読めない Spending Limit を「余裕あり」とみなさない**（不在を黙って通さない）。

**ローカル開発の土台は実装済み。** `docker/Dockerfile` / `docker/docker-compose.yml` を使い、
`make setup` で Python 3.12・Braket SDK・開発ツールを構築する。
ベースイメージは digest、Python 依存は `docker/requirements.txt` で固定。
`make sim-smoke` は Bell 回路の動作確認、`make check` は ruff / mypy / pytest、
`make shell` は同じコンテナの bash。ローカル実行はネットワーク無効・AWS 認証不要。
`src/`、`tests/`、`runs/` と必要な設定だけをコンテナへ mount する。
`src/shor_braket/runner/local.py` はローカル実行のみを担い、validated レコードは発行しない。
`make sim` / `sim-all` は N=15 / N=6 の行列参照回路を実行して教材を生成する。
`make validate-n15` がレコードを発行し、`make preflight` が無料のドライランを回す。`submit-sv1` は廃止した。

**2026-09-19: 実タスクの投入を実装した**（issue #17 の前半、`aws_session.py`、`runner/submit.py`、
`runner/task_status.py`、`make preflight` / `submit-qpu` / `task-status`）。**お金が動く経路は
`docker-compose.yml` の `aws` サービス 1 つだけ**で、`local` は `network_mode: none` のまま据え置く。
`aws` はホストの `~/.aws` を読み取り専用で `/aws` にマウントし、`HOME` ではなく `AWS_CONFIG_FILE` で指す。
読み取り専用なので botocore はロールをキャッシュできず、**MFA プロンプトは実行ごとに 1 回**出る（意図的）。
デバイス→プロファイルの対応は `aws_session.submission_profile_env` が持つ（ibex → AQT ロール、他 → 実行ロール）。
`submit()` は preflight が通った計画しか受け取らず、送信直前に**回路を再ハッシュ**してレポートが承認した
program であることを確かめる。`disable_qubit_rewiring` は `False`（verbatim と併用するとサービスが拒否する）。
作成したタスクは `runs/raw/qpu-*/submission.json` に台帳として残る（gitignore 対象なのでアカウント ID を書ける）。
**ドライランは Spending Limit を読めない**ので、`--no-execute` のときだけ判定を「資格情報なしで答えられる
検査がすべて通ったか」に読み替える。これは表示と終了コードにしか効かず、`submit()` の `PermissionError` は無条件。
**2026-09-23: 経路確認 1 回目は IAM で拒否された（課金なし）。** preflight は Spending Limit 込みで全項目 PASS
（Garnet constant の λ は再校正で 0.564）、`CreateQuantumTask` が guardrail の `DenyTaskCreationWithoutMfa` で
`AccessDenied`。原因と修正は下の AWS 節と `infra/iam/README.md` §4.4（`fix/role-session-mfa-deny`）。
**同日、修正の apply（`iam-verify` 26/26）後に経路確認が通った。** Garnet・generic-constant・10 shots、
作成から完了まで約 5 秒、課金は見積りどおり 0.3145 USD（Spending Limit の `totalSpend`）、タグ `project` / `oracle` がタスクに付いた。
`make report` の λ は **0.333 ± 0.211**（予測 0.564、差は −1.1σ、ゼロからは +1.6σ）で、**10 shots では予測とも一様乱数とも
区別できない**。これは経路確認として想定どおりで、物理の測定ではない。デバイスは測定 qubit を**昇順でなく**、
SWAP の中継 qubit も含めて返した（`[19, 15, 10, 18, 14, 20, 16]`）。解析は結果の `measuredQubits` を使うので影響なし。
**`make report` も実装した**（`runner/report.py`、`visualization/qpu.py`）。結果 JSON を投入記録の
`register_layout` で count + work の同時分布に直し、理想分布および**エミュレーションが予測した λ** と比較する。
**λ の推定量は 2 つあり、合否に使うのはサポート質量由来のほう**（カウントに線形なのでショット数によらず不偏）。
TVD 由来の λ は有限ショットで標本床のぶん沈むので、床と並べて表示するだけで判定には使わない
（10 ショットの経路確認ではこの偏りが支配的になる）。`--result-file` で S3 を介さずオフライン解析できる。

### 1. サービス名は Amazon **Braket**（Bracket ではない）

コード・ドキュメント・パッケージ名はすべて `braket` で統一する。

### 2. N = 6 では Shor の量子部分が分解に寄与しない

$\mathbb{Z}_6^* = \{1, 5\}$ で使える底は a = 5 のみ。$5^2 \equiv 1 \pmod 6$ より r = 2 だが、
$a^{r/2} = 5 \equiv -1 \pmod 6$ となり Shor の成功条件を満たさない。
得られる因数 2 は「6 が偶数である」ことに等しい。詳細は `docs/01-why-n6-is-degenerate.md`。

**このプロジェクトで N = 6 の実行結果を「Shor による因数分解の成功」と記述してはならない。**
正しくは「a = 5 mod 6 の位数 r = 2 の量子位相推定による測定」。

**アルゴリズムの検証は N = 15（a = 7, r = 4 など）を主テストケースとして行う。** N = 6 は底が 1 つで
制御-U も実質 1 個しか働かず、QPE の配線ミスを検出できない。N = 6 は縮退ケースの回帰テストとして併設し、
「位数発見が分解に寄与したか」が常に false になることを確認する。

- **N = 15 でも「因数が出た」を成功条件にしない。** 連分数展開の保証窓は t に依存せず、一様乱数を返す
  デバイスでも 1 ショットあたり約 12% で 3 × 5 が得られる。評価指標は理想分布との TVD / Hellinger fidelity
- **実機ペイロードは Phase 2 でトランスパイル後の 2 qubit ゲート数を実測してから決める。** 設計段階の
  見積りでは N = 15 generic は数千ゲートで完走せず、N = 6 generic t = 1 が数十ゲートで下限ぎりぎり

### 3. 課金が発生する

Braket の QPU は「タスクあたり定額 + ショットあたり従量」。
**実機に投げるコードを書くときは必ず実行ゲート（`docs/03-execution-gate.md`）を経由させる。**
ゲートを迂回するコードパスを追加しない。テストで実デバイスを叩かない。

### 4. コンパイル済みオラクルを黙って使わない

位数 r を知った上で回路を最適化すると「答えを回路に埋め込む」ことになる
（Smolin et al. 2013 の批判）。オラクルモード（`generic-repeated` / `generic-constant` /
`compiled`）を必ず実行記録に残す。既定は `generic-repeated`。

---

## 開発ルール

- Python: PEP 8、関数 snake_case、クラス PascalCase、定数 UPPER_SNAKE_CASE、Docstring は Google Style
- コード内のコメント・ログ・エラーメッセージ・docstring は **英語**
- 応答・設計ドキュメントは **日本語**
- ブランチ: `feature/*`, `fix/*`, `refactor/*`。PR は main へ
- セッション記録は `.claude-notes/`（gitignore 対象）

### コミットメッセージ

英文・動詞始まり。絵文字プレフィクス:
✨ feat / 🐞 fix / 📚 docs / 🎨 style / 🛠️ refactor / ⚡ perf / ✅ test / 🏗️ chore /
🚀 deploy / 🔒 security / 📝 update / 🗑️ remove

Claude Code 経由のコミットには以下を含める:

```text
🤖 Assisted by [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

### ドキュメント同期

機能追加・Phase 完了時に以下を同期更新する:

1. `CLAUDE.md` — プロジェクト全体状況、Phase 完了記録、技術仕様
2. `README.md` — 機能概要、実装状況、使用方法、ステータス表
3. `Makefile` / `makefiles/*.mk` — `##` ヘルプテキスト

---

## 実装上の指針

- `quantum/` は `Circuit` を返すだけで実行しない。実行は `runner/` の責務
- 古典参照実装（`classical/order.py`）が常に真値を提供する。量子側の結果はこれと突き合わせる
- 回路ハッシュは OpenQASM のテキストではなく **正規化した Braket IR** に対して取る（`gate/circuit_hash.py`）。
  **target の順序を sorted にしない**（制御と標的が入れ替わった回路が同じハッシュになる）。
  正規化の規則を変えたら `CANONICAL_FORM_VERSION` を上げる
- **クライアント側のゲートに検査を足しても防御は増えない。** `gate/preflight.py` は事故防止であって
  セキュリティ境界ではない。AWS 側（IAM Deny、Spending Limit）から検査を移してこない
- デバイス ARN をハードコードしない。論理名 → ARN のマップを設定ファイルに置き、
  起動時に `search-devices` で実在検証する
- Braket SDK を第一級とする。Qiskit からの変換レイヤは挟まない
- `LocalEmulator` は verbatim box 必須・ネイティブゲートのみ・物理 qubit と接続性を検査する。SDK に
  トランスパイラは無いので、ネイティブ分解は `quantum/native.py` に手書きし `to_unitary` で検証する
- エミュレーション結果（`execution.class = local-emulator`）を validated レコードにしない。結果には
  スナップショットの `capabilities_sha256` と `calibration_updated_at` を残し、古い校正の結果を現在値として扱わない
- IQM の CNOT は `prx` 4 枚 + `cz` 1 枚。qubit 選択は 2 qubit 忠実度だけでなく参加 qubit の 1 qubit RB も見る
- ルーティングと配置は `quantum/routing.py` の自前貪欲ルータ（近傍 × 全順列で誤り予算最小）。誤り予算 B の総和で
  信号残存率 λ ≈ exp(−B) が予測できる。Qiskit のトランスパイラは使わない
- Braket の `Probability(target=...)` は昇順でない target 順を守らない。昇順で取って自分で並べ替える
  （`runner/n15.py` の `reorder_probabilities`）。全結合機は接続グラフが空で `ResultTypeValidator` が
  `Probability` を拒否するので、解析用のノイズ付き回路は `noise_model.apply` で作る
- t=2 では count register の周辺分布は理想でも一様。評価は必ず count + work の同時分布で行う
- **feed-forward 回路（`measure_ff` / `cc_prx`）のノイズ付き実行に SDK の shot 毎シミュレーションを使わない。**
  default-simulator 1.40.1 の分岐実行は 1 qubit depolarizing と測定後の bit-flip を落とす（`LocalEmulator.run` も同じ）。
  厳密分布は `quantum/feedforward.py` の deferred measurement 変換で `braket_dm` の `Probability` から出し、標本はそこからの
  多項サンプリングで作る。エミュレータのノイズモデルも `measure_ff` / `cc_prx` にノイズを付けないので、読み出し bit-flip と
  1q depolarizing を `runner/n15_iterative.py` で手で足す
- `braket_dm` の large カーネルは制御 qubit が末尾軸にある制御付き 1 qubit ゲートで落ちる。制御付きゲートは 2 qubit ユニタリで書く
- 実機の dynamic circuit 制約（キー一意、`cc_prx` は `measure_ff` の後、制御元は 1 qubit、同一 qubit グループ内、verbatim）のうち
  グループはオフラインで検査できない。投入前にデバイスページで確認する

---

## 秘密情報の扱い

**アカウント ID・バケット名・通知先メールアドレス等をリポジトリに書かない。**

| 用途 | 置き場所 | git |
|---|---|---|
| クライアント / Makefile | `.env`（テンプレート `.env.example`） | ignore |
| Terraform | `infra/terraform/terraform.tfvars`（テンプレート `*.tfvars.example`） | ignore |
| IAM ポリシーの実値展開 | `infra/iam/rendered/`（`make iam-render` が生成） | ignore |
| Terraform ステート | `infra/terraform/terraform.tfstate` | ignore |

`infra/iam/*.json` にはプレースホルダ（`<AWS_ACCOUNT_ID>` / `<RESULTS_BUCKET>`）のみ。
値を直書きしたコードやコマンド例をドキュメントに残さない。
コマンドライン引数で秘密値を渡す例も避ける（シェル履歴に残るため）。

---

## AWS

- **root アカウントのアクセスキーを使わない。** 2026-09-13 時点で `default` プロファイルが root キーだった。
  `infra/iam/README.md` §11 の bootstrap を先に行う: `admin-base` + MFA + `AdminRole` を作り、
  動作確認してから `terraform-admin`（未使用の管理者）と root キーを廃止する。順番を守ること
- **Terraform は `AWS_PROFILE_ADMIN`（MFA 必須の assume role）で実行する。** `make tf-plan` / `tf-apply` は
  呼び出し元が root なら拒否する。管理者プリンシパル・アクセスキー・MFA デバイスは Terraform に入れない
- **IAM プリンシパルは Terraform で作る**（`infra/terraform/iam.tf`）。ポリシー本文は `infra/iam/*.json` が唯一の定義で、
  Terraform は `file()` + `replace()` で読み込む。名前の不整合は `precondition` が plan 時に止める
- **IAM プリンシパルを 3 つに分ける**（`infra/iam/README.md`）
  - `shor-braket-monitor` — 監視専用ユーザー。閲覧のみで assume の Allow を持たない
  - `shor-braket-operator` — 操作者ユーザー。読み取り + 実行ロールへの assume
  - `ShorBraketExecutionRole` — タスク投入。**MFA 必須の assume role**
  - 操作者端末のプロファイルは `shor-braket-ro`（読み取り）と `shor-braket-exec`（投入）。
    読み取り系ターゲットは RO、`submit-*` は EXEC を使う
- **ユーザー側の AssumeRole 許可に MFA 条件を付けない。** MFA は信頼ポリシー側で強制する。
  長期キーのリクエストには `aws:MultiFactorAuthPresent` が存在せず、ユーザー側の条件は
  効かないか assume を壊すだけ
- **デバイス制限は `Allow` ではなく `Deny` で書く。** Braket の IAM リソースタイプは
  `quantum-task` のみで、デバイスは `Allow` の `Resource` で絞れない（AWS 公式仕様）
- MFA 判定は `Bool` ではなく **`BoolIfExists`** を使う。長期アクセスキーでは
  `aws:MultiFactorAuthPresent` キー自体が存在せず、`Bool` だと Deny が発動しない。
  **この Deny はユーザー専用の `shor-braket-user-guardrail` に置き、ロールには付けない**
- **ロールに付くポリシーで `aws:MultiFactorAuthPresent` を参照しない**（2026-09-23 実測）。MFA 付きで assume した
  ロールのセッションでも、セッション内ではこのキーが偽として評価される（CloudTrail の `mfaAuthenticated: false`、
  CLI でも boto3 でも同じ）。ロールに MFA の Deny を付けるとロールは何もできず、Allow に MFA 条件を付けると暗黙の拒否になる。
  MFA は信頼ポリシーだけで強制する（MFA なしの assume が `AccessDenied` になることを実測済み）。
  `iam.tf` の precondition が、ロールに付くポリシーにこのキーが入ったら plan で止める
- **シミュレータに渡す文脈は実物に合わせる。** `iam-verify` は 2026-09-22 までロールを MFA=true で模擬していて、
  22/22 のまま実機投入が拒否された。今はロールを MFA=false で模擬し、MFA は実際の AssumeRole プローブで確かめる
- **AQT は専用ロールに分ける**（2026-09-15、ADR-0004）。以前はリクエストタグ
  `campaign=device-comparison` で Deny を開けていたが、**`aws:RequestTag` は呼び出し側が自分の
  リクエストに乗せる値**なので、実行ロールは自分に掛かった Deny を自分で外せた。境界にならない。
  現在は `ShorBraketExecutionRole` が AQT を無条件 Deny、`ShorBraketAqtRole` が IQM を無条件 Deny する。
  高額機に触る行為は独立した `AssumeRole` として CloudTrail に残る。1 タスクで予算超過しうる機種
  （IonQ 等）は共通ガードレールで無条件 Deny のまま（`infra/iam/README.md` §6）
- **タグは認可に使わない。** `project` と `oracle` を常時、`campaign` を実行ごとに任意で付ける。
  用途はコスト配分と監査のみ。ロールが能力を分け、タグは会計を束ねる
- **SV1 / DM1 は使わない**（2026-09-15、ADR-0004）。**SV1 は verbatim 回路を実行できない**ため、
  validated レコードを発行した回路そのものを投げられない。AWS 経路の確認は Garnet に 10 ショット
  （0.3145 USD）を投げて行う。**結果バケットは eu-north-1 の 1 つだけ。** eu-west-2 は使わない
- **ショット数を制限する IAM 条件キーは存在しない。** クライアント側 + AWS Budgets で守る
- Terraform で管理するもの: S3（結果保存、**eu-north-1 の 1 つだけ**、`amazon-braket-` 必須、Glacier 移行なし）、IAM、
  AWS Budgets + SNS、Braket Spending Limit × 3（awscc、初期値 0 USD、合計 300 USD は local 定数、`prevent_destroy`）、
  コスト配分タグの有効化（変数で 2 段 apply）
- Terraform で管理しないもの: 量子タスク（使い捨ての実行単位であり状態管理対象として不適切）、
  CloudWatch ロググループ（量子タスクはログを書かない。監査は CloudTrail）
- **Spending Limit の変更は Terraform だけ。** 全プリンシパルが `SearchSpendingLimits` のみ許可、Create / Update / Delete は
  guardrail で Deny。期間は両端必須で、初回は未設定。実験ごとに tfvars の `spending_limits` を書き換えて apply する
- `.terraform.lock.hcl` はコミットする（provider のハッシュ固定。アカウント情報は入らない）
- **Terraform ステートはローカル管理**（`backend.tf` を置かない）
- Braket の初回有効化（コンソールでの利用規約同意）は Terraform 不可。手動手順として記録する

---

## 決定事項

| 項目 | 決定 | 記録 |
|---|---|---|
| 対象 N | N = 15 を検証の主題、N = 6 を縮退ケースとして併設。実機ペイロードは Phase 2 の深さ実測で決定 | `docs/adr/0001-*.md` |
| ライセンス | Apache License 2.0 | `LICENSE` |
| Terraform ステート | ローカル管理 | `docs/adr/0002-*.md` |
| 月次予算 | **100 USD** | `docs/adr/0002-*.md` |
| 第一候補デバイス | IQM Garnet（feed-forward 対応 + 低単価） | `docs/04-devices-and-cost.md` |
| AQT IBEX Q1 の扱い | **専用ロール `ShorBraketAqtRole`**（2026-09-15 にタグゲートから変更） | `docs/adr/0004-*.md` |
| IAM プリンシパル | 監視ユーザー / 操作ユーザー / 実行ロール / AQT ロール の 4 分割（ロールは MFA 必須） | `docs/adr/0002-*.md`、`docs/adr/0004-*.md` |
| マネージドシミュレータ | SV1 / DM1 とも使わない。SV1 は verbatim 非対応 | `docs/adr/0004-*.md` |
| リージョン | eu-north-1 のみ。結果バケットも 1 つ | `docs/adr/0004-*.md` |
| タグ | `project` / `oracle` を常時、`campaign` は任意。**認可には使わない** | `docs/adr/0004-*.md` |
| 反復 QPE | 比較軸として残すのみ。既定は標準 QPE（t=2 では λ が動かない） | `quantum/n15_iterative.py` docstring、issue #7 |
| 投入ゲートの判定 | エミュレーションの厳密 λ ≥ 0.5 で判定。誤り予算 B は目安として表示 | `docs/03-execution-gate.md` §4.1、issue #7 |
| 信号の検出 | 標本のサポート質量由来 λ が標準誤差の 3 倍を超えれば「信号あり」を別に記録。位数復元率は合否に使わない | `analysis/distribution.py` |
| ノイズ無しシミュレータの合否 | 厳密 TVD < 1e−9 かつ 標本 TVD ≤ 1.5 × Σ√(p(1−p)/(2πn))。両方を記録 | `runner/reference.py` |
| DM1 | 必須にしない（LocalEmulator で足りる。AWS 経路の確認は SV1） | issue #7 |
| 回路ハッシュ | 正規化した IR の JSON に SHA-256。target 順は保存、角度は 12 桁、verbatim と物理 qubit を含め、shots は含めない | `docs/03` §3.2、`gate/circuit_hash.py` |
| レコードの失効 | 回路ハッシュ / 校正ハッシュ / ARN / SDK major / 正規化版 / 30 日。実質の主判定は校正ハッシュ | `docs/03` §4.3 |
| Spending Limit が読めないとき | 拒否する（余裕とみなさない）。ドライランのみ表示上の読み替えあり | `gate/preflight.py`、`docs/03` §5.1 |
| お金が動く経路 | `docker-compose.yml` の `aws` サービス 1 つだけ。`local` は `network_mode: none` を維持（2026-09-19） | `docs/03` §5.1 |
| 投入時の資格情報 | ホストの `~/.aws` を読み取り専用マウント。MFA は実行ごとに 1 回（キャッシュしない）（2026-09-19） | `aws_session.py` |
| 投入記録 | `runs/raw/qpu-*/submission.json`。validated レコードとは別の台帳（2026-09-19） | `runs/README.md` |
| 実機の λ の推定量 | **サポート質量由来（不偏）で合否を判定**。TVD 由来は標本床と並べて表示のみ（2026-09-19） | `runner/report.py` |
| レジスタの物理 qubit | 投入記録に残す。校正が変わるとルータが別の配置を選ぶため後から再計算できない（2026-09-19） | `runner/submit.py` |
| Terraform の実行主体 | admin（MFA 必須の assume role）。plan は誰でも、apply / destroy は Syota さん本人 | `infra/terraform/README.md` |
| Budget のフィルタ | コスト配分タグ `project=shor-braket`。サービス単位にはしない（S3 / CloudWatch も使う） | issue #3 |
| 月次累計の取得元 | AWS Budgets の `CalculatedSpend`（無料、`budgets:ViewBudget`）。Cost Explorer は使わない | issue #7 |
| コスト配分タグの有効化 | Terraform（`aws_ce_cost_allocation_tag`）。キー出現まで約 24 時間。本実験は有効化から 24 時間後以降 | issue #4 |
| `default_tags` のキー | `project` を小文字にしてタスクのタグと揃える。`AmazonBraket` タグは廃止 | `infra/terraform/variables.tf` |
| CloudWatch ロググループ | 作らない。量子タスクはログを書かず、監査は CloudTrail（2026-09-16） | `infra/terraform/README.md` |
| S3 ライフサイクル | Glacier 移行なし。小さなオブジェクトでは overhead で逆に高い。実験後は NAS に写して destroy（2026-09-16） | `infra/terraform/README.md` |
| Spending Limit の期間 | 初回 apply は未設定、実験ごとに両端を設定（2026-09-16） | `infra/terraform/README.md` |
| Budget 通知 | SNS トピック + email サブスクリプション。確認クリックは手作業（2026-09-16） | `infra/terraform/budget.tf`、issue #4 |
| `.terraform.lock.hcl` | コミットする（2026-09-16） | `infra/terraform/README.md` |
| 経路確認時の Garnet の Limit | **5 USD**（10 ショット 0.3145 USD、再試行の余裕込み。stage 2 の apply で上げる） | issue #17 |
| 結果バケット名 | `amazon-braket-` プレフィクス必須（サービスリンクロールが書ける範囲）。validation で強制 | `infra/terraform/variables.tf` |

---

## Phase 進捗

| Phase | 内容 | 優先度 | 状態 |
|---|---|---|---|
| 0 | プロジェクト設計・ドキュメント | — | ✅ 2026-09-07 完了 |
| 1 | Shor 実装（古典前処理 + 位数発見回路） | **高** | ✅ 2026-09-13 N=15 行列参照回路（`local-reference`、QPU 投入不可） |
| 2 | ローカルシミュレータ検証と実行ゲート | **高** | ✅ 2026-09-14 完了。同時分布検証・可視化・LocalEmulator スパイク・N=15 QPU 互換回路のエミュレーション（3 機）・反復 QPE の比較と TVD 標本床の解析・validated レコードと投入ゲート |
| 3 | Terraform による AWS リソース定義 | **高** | ✅ **2026-09-16 apply 済み**（11 作成 / 3 in-place）。`iam-verify` 22/22、`search-spending-limits` が 3 機 0 USD、`describe-budget` が RO で読める。SNS は `--authenticate-on-unsubscribe` で決着。**2026-09-18 に stage 2 を apply**（`project` タグ有効化 + Garnet 5 USD / 2026-09-19〜09-28）。残るは stage 3 |
| 4 | ~~SV1 実行~~ | — | ❌ 廃止（ADR-0004）。AWS 経路の確認は Garnet 10 ショットで行う |
| 5 | 実機 QPU 実行と結果分析 | 低 | 🚧 2026-09-19 に投入と解析を実装。**2026-09-23 に経路確認が完了**（Garnet 10 shots、0.3145 USD、λ 0.333 ± 0.211）。本測定は未実施 |
