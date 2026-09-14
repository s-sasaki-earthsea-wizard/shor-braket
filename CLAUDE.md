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
2026-09-14 に実装完了。** 残る未決はタグ集合、レコードの有効期限の日数、`--yes` の運用、月次累計の取得元、
IBEX の実行ウィンドウ運用。**次の作業は AWS 側（issue #4 → #1 → #2 → #3）。**

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
`capabilities_sha256` の一致。**投入ゲートは回路側の検査を全部通り、止めているのは Spending Limit だけ**で、
これは issue #3 が開く。`submit()` は常に `NotImplementedError` を投げる。
**読めない Spending Limit を「余裕あり」とみなさない**（不在を黙って通さない）。

**ローカル開発の土台は実装済み。** `docker/Dockerfile` / `docker/docker-compose.yml` を使い、
`make setup` で Python 3.12・Braket SDK・開発ツールを構築する。
ベースイメージは digest、Python 依存は `docker/requirements.txt` で固定。
`make sim-smoke` は Bell 回路の動作確認、`make check` は ruff / mypy / pytest、
`make shell` は同じコンテナの bash。ローカル実行はネットワーク無効・AWS 認証不要。
`src/`、`tests/`、`runs/` と必要な設定だけをコンテナへ mount する。
`src/shor_braket/runner/local.py` はローカル実行のみを担い、validated レコードは発行しない。
`make sim` / `sim-all` は N=15 / N=6 の行列参照回路を実行して教材を生成する。
`make validate-n15` がレコードを発行し、`make submit-qpu` が投入ゲートを回す（タスクは作らない）。
`make submit-sv1` / `task-status` / `report` は未実装ガードを維持する。

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
  `aws:MultiFactorAuthPresent` キー自体が存在せず、`Bool` だと Deny が発動しない
- **AQT はタグで開ける Deny。** リクエストタグ `campaign=device-comparison` が無いと
  `CreateQuantumTask` を拒否する。1 タスクで予算超過しうる機種（IonQ 等）は無条件 Deny のまま
  （`infra/iam/README.md` §6）
- **ショット数を制限する IAM 条件キーは存在しない。** クライアント側 + AWS Budgets で守る
- Terraform で管理するもの: S3（結果保存）、IAM、AWS Budgets + SNS、CloudWatch ロググループ
- Terraform で管理しないもの: 量子タスク（使い捨ての実行単位であり状態管理対象として不適切）
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
| AQT IBEX Q1 の扱い | タグゲート付き Deny（`campaign=device-comparison` で開く） | `docs/adr/0002-*.md` |
| IAM プリンシパル | 監視ユーザー / 操作ユーザー / 実行ロール(MFA 必須) の 3 分割 | `docs/adr/0002-*.md` |
| 反復 QPE | 比較軸として残すのみ。既定は標準 QPE（t=2 では λ が動かない） | `quantum/n15_iterative.py` docstring、issue #7 |
| 投入ゲートの判定 | エミュレーションの厳密 λ ≥ 0.5 で判定。誤り予算 B は目安として表示 | `docs/03-execution-gate.md` §4.1、issue #7 |
| 信号の検出 | 標本のサポート質量由来 λ が標準誤差の 3 倍を超えれば「信号あり」を別に記録。位数復元率は合否に使わない | `analysis/distribution.py` |
| ノイズ無しシミュレータの合否 | 厳密 TVD < 1e−9 かつ 標本 TVD ≤ 1.5 × Σ√(p(1−p)/(2πn))。両方を記録 | `runner/reference.py` |
| DM1 | 必須にしない（LocalEmulator で足りる。AWS 経路の確認は SV1） | issue #7 |
| 回路ハッシュ | 正規化した IR の JSON に SHA-256。target 順は保存、角度は 12 桁、verbatim と物理 qubit を含め、shots は含めない | `docs/03` §3.2、`gate/circuit_hash.py` |
| レコードの失効 | 回路ハッシュ / 校正ハッシュ / ARN / SDK major / 正規化版 / 30 日。実質の主判定は校正ハッシュ | `docs/03` §4.3 |
| Spending Limit が読めないとき | 拒否する（余裕とみなさない） | `gate/preflight.py` |

---

## Phase 進捗

| Phase | 内容 | 優先度 | 状態 |
|---|---|---|---|
| 0 | プロジェクト設計・ドキュメント | — | ✅ 2026-09-07 完了 |
| 1 | Shor 実装（古典前処理 + 位数発見回路） | **高** | ✅ 2026-09-13 N=15 行列参照回路（`local-reference`、QPU 投入不可） |
| 2 | ローカルシミュレータ検証と実行ゲート | **高** | ✅ 2026-09-14 完了。同時分布検証・可視化・LocalEmulator スパイク・N=15 QPU 互換回路のエミュレーション（3 機）・反復 QPE の比較と TVD 標本床の解析・validated レコードと投入ゲート |
| 3 | Terraform による AWS リソース定義 | **高** | 🚧 **次はここ**。IAM は構築済み（2026-09-13）。残りは issue #4 → #1 → #2 → #3 |
| 4 | SV1 実行 | 低 | ⬜ |
| 5 | 実機 QPU 実行と結果分析 | 低 | ⬜ |
