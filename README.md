# shor-braket

Shor のアルゴリズムの位数発見回路を実装する。検証の主題は **N = 15 = 3 × 5**、当初要件の **N = 6 = 2 × 3** は縮退ケースとして併設する。まずローカルシミュレータで、検証を通ったものだけを **Amazon Braket** の実機 QPU に投げる。

AWS リソースは Terraform で管理し、**「ローカルシミュレータのテストをパスした計算のみ実機実行できる」** という実行ゲートをコード側とIAM側の両方で担保する。

> **注**: サービス名は Amazon **Braket**（ブラケット記法 ⟨bra|ket⟩ に由来）。

---

## ステータス

| フェーズ | 内容 | 優先度 | 状態 |
|---|---|---|---|
| Phase 0 | プロジェクト設計・ドキュメント | — | ✅ 完了 |
| **Phase 1** | **Shor アルゴリズム実装（行列参照回路 + 位数・因数復元）** | **高** | ✅ N=15 を実装 |
| **Phase 2** | **ローカルシミュレータ検証と実行ゲート** | **高** | 🚧 同時分布検証・結果保存・LocalEmulator 互換性スパイク・N=15 の QPU 互換回路（swap network、3 機でエミュレーション）まで実装。validated レコードと投入ゲートは未着手 |
| Phase 3 | Terraform による AWS リソース定義 | 次 | ⬜ IAMは構築済み。S3 / Budgets / Spending Limitは[#3](https://github.com/s-sasaki-earthsea-wizard/shor-braket/issues/3) |
| Phase 4 | Braket オンデマンドシミュレータ (SV1) 実行 | 低 | ⬜ 未着手 |
| Phase 5 | 実機 QPU 実行と結果分析 | 低 | ⬜ 未着手 |

**2026-09-14: N=15のローカル参照実装が完成。** AWS側はIAMまで構築済みで、SV1 / DM1の前提となる
S3、Budgets、Spending Limitを[#3](https://github.com/s-sasaki-earthsea-wizard/shor-braket/issues/3)で追跡する。
ローカルシミュレータは無料でAWS認証も不要なので、`make setup`だけで開発を始められる。

現時点では Docker 開発環境に加え、N=15, a=7 の行列参照回路、解析・サンプリング実行、
連分数による位数復元、3 × 5 の導出、同時分布検証、JSON 結果保存を実装済み。
この参照回路は密行列を使うため **QPU 投入不可**。QPU 互換回路と投入ゲートは別実装とする。

**2026-09-14: LocalEmulator 互換性スパイク完了。** IQM Garnet / Emerald、AQT IBEX Q1 の校正データを
`devices/snapshots/` にコミットし、`make emulate-all` が Docker 内（ネットワーク無効）で verbatim 検証と
校正ノイズ付き実行を行う。密行列参照回路は verbatim の有無にかかわらず拒否され、ネイティブゲートで
手書きした Bell / GHZ だけが通る。解説は Wiki
[LocalEmulator で実機の手前まで](https://github.com/s-sasaki-earthsea-wizard/shor-braket/wiki/Local-Emulator-Compatibility-Report)。

**2026-09-14: N=15 の QPU 互換回路を 3 機の LocalEmulator で実行。** $15 = 2^4 - 1$ を使う swap network で
モジュラー乗算を組み（`generic-constant`、t=2、6 qubit、論理 2 qubit ゲート 46）、自前の貪欲ルータで格子に配置し、
仮想 Z でネイティブ化して verbatim 実行した。周期 4 の信号残存率は Emerald 0.73、Garnet 0.54、IBEX Q1 0.39。
**これは因数分解の実験ではなく**、N=15 専用の分解と $t = 2$ という手掛かりの下で信号がどれだけ残るかの観察である。
解説は Wiki [N=15 を QPU 互換回路で](https://github.com/s-sasaki-earthsea-wizard/shor-braket/wiki/Shor-N15-on-Local-Emulator)。

---

## ⚠️ 最初に読むべきこと: N = 6 は Shor が「効かない」ケース

このプロジェクトの前提として、次の事実を明示しておく。詳細は [`docs/01-why-n6-is-degenerate.md`](docs/01-why-n6-is-degenerate.md)。

- $\mathbb{Z}_6^* = \{1, 5\}$、$\varphi(6) = 2$。よって $1 < a < 6$ で $\gcd(a, 6) = 1$ となる底は **a = 5 のみ**
- $5^2 = 25 \equiv 1 \pmod 6$ → 位数 $r = 2$
- しかし $a^{r/2} = 5 \equiv -1 \pmod 6$

Shor の成功条件は「$r$ が偶数 **かつ** $a^{r/2} \not\equiv -1 \pmod N$」。N = 6 では唯一の底がこの条件を満たさない。
得られる $\gcd(5 - 1, 6) = \gcd(4, 6) = 2$ は $\gcd(-2, 6)$ すなわち **「6 が偶数である」という古典的に自明な情報**に等しく、量子部分（位数発見）は答えに寄与していない。

そのため、**アルゴリズムの検証は N = 15（a = 7, r = 4）を主テストケースとして行う**（[ADR-0001](docs/adr/0001-n15-primary-n6-degenerate.md)）。N = 15 は底 8 個のうち 6 個で成功し a = 14 で失敗するため、「失敗したら底を引き直す」確率的構造、連分数展開、gcd の後処理まで Shor の全ステップを通せる。N = 6 は縮退ケースの回帰テストとして併設し、「位数発見が分解に寄与したか」が常に false になることを確認する。

N = 6 を残す理由:

1. **当初要件** — このプロジェクトの出発点は「6 = 2 × 3 を Shor で計算する」だった。
2. **回路が小さい** — 作業レジスタ 3 qubit + カウントレジスタ 1〜2 qubit、制御-U は 1 回。現行 NISQ 機で generic オラクルのまま完走しうる数少ないサイズで、実機ペイロードの候補。
3. **本題は AWS 側の運用設計** — Terraform によるリソース管理と実行ゲートの構築が主目的であり、量子回路はそのペイロード。

**実機に投げるペイロードは Phase 2 でトランスパイル後の 2 qubit ゲート数を実測してから決める。** 設計段階の見積りでは、N = 15 の generic オラクルは数千ゲートで実機では完走せず、N = 6 の generic t = 1 が数十ゲートで下限ぎりぎり。文献の N = 15 実機デモがほぼすべて compiled オラクルなのはこのためで、本プロジェクトでは compiled の結果を「Shor の実行」とは記録しない。

なお **N = 15 でも「因数が出た」は成功条件にならない。** 連分数展開の保証窓は t に依存しないため、一様乱数を返すデバイスでも 1 ショットあたり約 12% で 3 × 5 が得られる。評価指標は理想分布との TVD / Hellinger fidelity とする。

---

## 設計方針

### 1. 「コンパイル済みオラクル」を使わない

Shor の実機デモの多くは、位数 $r$ を**あらかじめ知った上で**モジュラー冪乗回路を最適化しており、実質的に答えを回路に埋め込んでいる（Smolin, Smith, Vargo による "oversimplifying quantum factoring" 批判）。

本プロジェクトでは制御モジュラー乗算を汎用に構成し、`--oracle {generic,compiled}` で明示的に切り替える。**compiled モードで得た結果は「Shor を実行した」とは記録しない。** 実行記録には必ずどちらのモードかを残す。

### 2. 実行ゲート: シミュレータをパスした回路のみ実機へ

```
   ┌─────────────────────┐
   │ 回路生成             │  build → OpenQASM 3 に正規化
   └──────────┬──────────┘
              │  circuit_hash = sha256(qasm)
   ┌──────────▼──────────┐
   │ ローカルシミュレータ  │  braket.devices.LocalSimulator
   │ + 判定アサーション    │  分布・位数復元・因数の正しさを検証
   └──────────┬──────────┘
              │  pass → runs/validated/<circuit_hash>.json を発行
   ┌──────────▼──────────┐
   │ submit CLI          │  同一 hash の validated レコードが
   │                     │  無ければ実機投入を拒否
   └──────────┬──────────┘
              │
   ┌──────────▼──────────┐
   │ Amazon Braket       │  SV1 → QPU
   └─────────────────────┘
```

多層で守る:

| 層 | 手段 | 防ぐもの |
|---|---|---|
| クライアント | validated レコード必須 + 回路ハッシュ一致 | 未検証回路の誤投入 |
| IAM (TF) | `braket:CreateQuantumTask` を特定デバイス ARN に限定 | 想定外の高額デバイス使用 |
| AWS Budgets (TF) | 月次予算閾値 + SNS 通知 | 課金の暴走 |
| Braket Spending Limit (TF) | 3 機の合計上限 300 USD、初期値 0 USD | QPU タスク作成時のハードストップ |
| クライアント | ショット数上限・推定コストの事前表示と確認 | 桁間違いの投入 |

回路ハッシュは OpenQASM 3 のテキストではなく **正規化した Braket IR** に対して取る（空白・命令順の揺れで hash が変わるのを防ぐ）。詳細は [`docs/03-execution-gate.md`](docs/03-execution-gate.md)。

### 3. Terraform で管理するもの

- 結果保存用 S3 バケット（`amazon-braket-*` プレフィクス）+ ライフサイクルポリシー
- Braket 実行用 IAM ロール / ポリシー（デバイス ARN 限定、最小権限）
- AWS Budgets + SNS トピック（コスト警告）
- CloudWatch ロググループ

**管理しないもの**: 量子タスクそのもの。タスクは使い捨ての実行単位であり、Terraform の状態管理対象として不適切。SDK から投入し、結果は S3 に落とす。

---

## ディレクトリ構成

```
shor-braket/
├── README.md
├── CLAUDE.md                    # Claude Code 向けプロジェクト指示
├── LICENSE                      # Apache License 2.0
├── .env.example                 # 環境変数テンプレート（.env は gitignore）
├── pyproject.toml
├── .dockerignore                # ビルドに必要なファイルだけを送る
├── docker/
│   ├── Dockerfile               # Python 3.12 + Braket SDK + 開発ツール
│   ├── docker-compose.yml       # ローカル実行環境
│   └── requirements.txt         # Docker 内の依存バージョン
├── Makefile
├── makefiles/                   # 分割した make ターゲット
├── devices/
│   └── snapshots/               # 3 機の校正データ（GetDevice の保存。公開情報のみ、コミット対象）
├── docs/
│   ├── 01-why-n6-is-degenerate.md   # N=6 の数論的縮退の詳細
│   ├── 02-architecture.md            # モジュール構成と回路設計
│   ├── 03-execution-gate.md          # 実行ゲートの仕様
│   ├── 04-devices-and-cost.md        # Braket デバイスと課金
│   └── adr/                          # Architecture Decision Records
├── src/shor_braket/             # 参照回路、N=15 QPU 互換回路、ルータ、ネイティブ化、古典後処理、CLI、runner
├── tests/                       # pytest
├── infra/
│   ├── iam/                     # IAM ポリシー JSON（Deny ガードレール込み）
│   └── terraform/               # IAM 実装済み。S3 / Budgets / Spending Limit は未実装
└── runs/
    ├── validated/               # 検証済みレコード（コミット対象）
    └── raw/                     # 生の測定結果（gitignore）
```

---

## セットアップ

### Phase 1–2（ローカルのみ・AWS 不要）

ホスト側に必要なのは **Docker Engine / Docker Desktop、Docker Compose v2、make**。
Docker を起動してから、リポジトリのルートで実行する。ホストの Python 環境は不要。

```bash
make setup                      # requirements.txt から Docker イメージを構築
make sim-smoke                   # Bell 回路を 1000 shots で実行
make sim-smoke SHOTS=256         # ショット数を指定
make sim                         # N=15 を因数分解し、過程を SVG/PNG/HTML で可視化
make sim SHOTS=256               # shots を変更して実行
make qpu-costs SHOTS=1000        # 将来の QPU 候補 3 機の概算を表示（AWS 接続なし）
make emulate DEVICE=garnet       # 校正スナップショットから LocalEmulator を組み、ネイティブ回路を検証・実行
make emulate-all                 # 3 機の比較図と report.md を生成（オフライン）
make device-info DEVICE=garnet   # スナップショットの qubit 数・忠実度・価格・実行窓を表示
make emulate-n15                 # N=15 の QPU 互換回路を 3 機 × 2 oracle でエミュレーションし信号残存を比較
make check                      # ruff + mypy + pytest
make test-cov                   # カバレッジ（runs/coverage/index.html に出力）
make shell                      # 同じ環境の bash に入る（exit で終了）
```

`sim-smoke` は 2 qubit の Bell 状態を測定し、`00` / `11` の測定回数を JSON で表示する。
測定回数の内訳は実行ごとに変わる。テストでは shots=0 の厳密な確率が
`[0.5, 0, 0, 0.5]` となることも確認する。
これは環境の動作確認であり、Shor の位数発見は `make sim` で行う。結果は
`runs/raw/local-n15-a7-*/result.json` に保存される。解析実行（shots=0）で回路の理想同時分布を検証し、
指定 shots のサンプリング結果と将来の QPU 費用概算も同じレポートに残す。

同じrunディレクトリにはMatplotlibで生成した `walkthrough.html`、`walkthrough.md` と
`figures/` が作られる。HTMLをブラウザで開くと、次の順で `15 = 3 × 5` に至る過程を追える。

1. Shor全体の古典・量子パイプライン
2. count/work registerと量子回路の役割
3. モジュラー累乗列の周期
4. Hadamard、モジュラー累乗、逆QFT後の同時確率
5. 理想的な測定ピークと有限shotsの標本頻度
6. 連分数、位数候補、2本のgcd計算による因数復元

SVGは拡大しても数式やラベルが鮮明な教材用、PNGはスライドなどへ貼りやすい形式だ。
各図は `result.json` 内のそのrunの値から生成される。`--no-visualize` を指定すると図の生成だけを省略できる。

`make sim-all` は N=15 に加えて N=6 の縮退ケースも実行する。N=6 では位数2を復元できるが、
$a^{r/2} \equiv -1 \pmod 6$ のため量子部分から非自明な因数は得られないことを確認する。

`make emulate DEVICE=garnet` は `devices/snapshots/garnet.json` から Braket SDK の `LocalEmulator` を組み、
密行列参照回路・教科書どおりの Bell・ネイティブゲートの Bell / GHZ を検証行列にかけたうえで、
通った回路を校正ノイズ付き density-matrix シミュレータで実行する。結果は
`runs/raw/emulator-garnet-*/result.json` と `figures/` に保存され、`make emulate-all` は 3 機分に加えて
`runs/raw/emulator-comparison-*/` に比較図と `report.md` を書く。これらは Shor の回路ではなく互換性の
確認であり、validated レコードは発行しない。校正データの再取得は `make device-snapshot`
（`GetDevice` のみ・課金なし・読み取りプロファイル）。

ローカル実行用コンテナはネットワークを無効化して動かす。**AWS 認証や `.env` は不要。**
Docker イメージの初回ビルドと依存更新時にはネット接続が必要。

`src/`、`tests/`、`runs/` と設定ファイルを `/workspace` 配下へ個別に bind mount する。
これによりコード変更は即座に反映され、整形結果・カバレッジ・シミュレーション結果は
ホストに残る。AWS 資格情報やリポジトリ全体はコンテナへ渡さない。
make はホストの UID/GID でコンテナを実行し、各コマンドの終了時にコンテナを削除する。

Python ベースイメージは digest で固定し、実行時・テスト・静的解析を含む依存関係は
`docker/requirements.txt` に固定バージョンで定義する。依存関係を変更するときは
requirements と、パッケージの実行時依存を表す `pyproject.toml` を必要に応じて更新する。

```bash
# Edit docker/requirements.txt and pyproject.toml when needed.
make setup
make check
```

ベースイメージを更新する場合は `docker/Dockerfile` のタグと digest を合わせて更新する。
Compose の残ったコンテナやネットワークは `make docker-down` で片付けられる。

参考: [Braket のローカルシミュレータ](https://docs.aws.amazon.com/braket/latest/developerguide/braket-send-to-local-simulator.html)。

### Phase 3 以降（AWS）

```bash
cp .env.example .env      # アカウント ID・バケット名・予算などはすべてここ
$EDITOR .env
```

**root のアクセスキーで作業しない。** 最初に [`infra/iam/README.md`](infra/iam/README.md) §11 の手順で
MFA 必須の管理者ロールを作り、root キーを削除する。`make tf-plan` / `tf-apply` は `AWS_PROFILE_ADMIN` で動き、
呼び出し元が root なら拒否する。IAM プリンシパルは Terraform で作る（[`infra/terraform/`](infra/terraform/)）。

`.env` を作れば `make` が自動で読み込む。`make help` の末尾で読み込み状態を確認できる。

**IAM プリンシパルは監視 / 操作 / 実行ロールの 3 つに分け、操作者端末のプロファイルは権限で 2 つに分ける。**

| プロファイル | 権限 | MFA | 用途 |
|---|---|---|---|
| `shor-braket-ro` | 読み取りのみ | 不要 | デバイス一覧・価格取得・結果閲覧 |
| `shor-braket-exec` | タスク投入 | **必須** | `submit-sv1` / `submit-qpu` |
| `shor-braket-monitor` | 読み取りのみ（**別 IAM ユーザー**、assume 権限なし） | 不要 | ダッシュボード・別端末・別の人 |

実装は IAM ユーザー + MFA 必須の assume role。`~/.aws/config` の書き方と
ポリシー JSON は [`infra/iam/README.md`](infra/iam/README.md)。

> 読み取りプロファイルで `submit` を叩くと IAM が `AccessDenied` を返す。これは仕様。
> クライアント側のゲートと違って**回避できない**防御なので、うっかり課金への実効的な歯止めになる。

> **初回のみ**: Amazon Braket はコンソールでの有効化（利用規約への同意）が必要。
> QPU ごとに利用可能リージョンも異なる。詳細は [`docs/04-devices-and-cost.md`](docs/04-devices-and-cost.md)。

### 秘密情報の扱い

**アカウント ID・バケット名・通知先メールアドレス等はリポジトリに書かない。**

| 用途 | 置き場所 | git |
|---|---|---|
| クライアント / Makefile | `.env`（テンプレート: `.env.example`） | ignore |
| Terraform | `infra/terraform/terraform.tfvars`（テンプレート: `*.tfvars.example`） | ignore |
| IAM ポリシーの実値展開 | `infra/iam/rendered/`（`make iam-render` が生成） | ignore |
| Terraform ステート | `infra/terraform/terraform.tfstate` | ignore |

`infra/iam/*.json` にはプレースホルダ（`<AWS_ACCOUNT_ID>` / `<RESULTS_BUCKET>`）のみを置き、
実値は `make iam-render` が `.env` から埋めて `rendered/` に出力する。

コマンドライン引数（`make iam-render ACCOUNT_ID=...`）でも上書きできるが、
**シェル履歴に残るため常用しないこと**。

---

## 使い方

```bash
make help          # 全ターゲットの一覧
make devices       # Braket デバイスの現況確認（無料・読み取りのみ）
make device-snapshot  # 3 機の校正データを devices/snapshots へ保存（GetDevice のみ・課金なし）
make validated     # 検証済みレコードの一覧
make iam-lint      # IAM ポリシー JSON の構文検証
make iam-render    # .env の値でプレースホルダを展開
make iam-verify    # IAM ガードレールの効果をポリシーシミュレータで検証（課金なし）
```

### 実行インタフェース

```bash
# 1. ローカルシミュレータで検証（無料・AWS 不要）
make sim N=15
make sim N=6 A=5 T=1
make qpu-costs SHOTS=1000

# 1b. 校正データ付き LocalEmulator で verbatim 回路を検証（無料・オフライン）
make emulate-all

# 1c. N=15 の QPU 互換回路（swap network, t=2）を 3 機でエミュレーション（無料・オフライン）
make emulate-n15

# 2. 検証済みレコードの確認
make validated

# 3. Braket オンデマンドシミュレータ (SV1) で実行
make submit-sv1 N=6

# 4. 実機 QPU に投入（validated レコードが必須。コスト確認プロンプトあり）
make submit-qpu N=6 DEVICE=garnet SHOTS=1000
```

---

## デバイスとコスト

**2026-09-07 実測**。詳細と注意点は [`docs/04-devices-and-cost.md`](docs/04-devices-and-cost.md)。

Braket の QPU は **タスクあたり定額 + ショットあたり従量** の二段課金。
採用する3機と1000ショットあたりの概算:

| 論理名 | デバイス | qubit | 全結合 | feed-forward | ショット単価 | 1000 shots 概算 |
|---|---|---|---|---|---|---|
| `garnet` | IQM Garnet | 20 | ✗ | **✓** | $0.00145 | **約 $1.75** |
| `emerald` | IQM Emerald | 54 | ✗ | **✓** | $0.0016 | 約 $1.90 |
| `ibex` | AQT IBEX Q1 | 12 | ✓ | ✗ | $0.0235 | 約 $23.80 |

> ⚠️ 3機の中でも1000 shotsの概算には13倍以上の差がある。
> かつての主力機（IonQ Aria、Rigetti Ankaa-3 など）は**すべて RETIRED** 済み。
> 実行前に `make devices` で必ず現況を確認すること。

**第一候補は IQM Garnet**。ショット単価が低く、かつネイティブゲートに `cc_prx` / `measure_ff` を
持つため**反復的 QPE（ミッドサーキット測定 + フィードフォワード）が実装できる**。
N = 6 なら合計 4 qubit で済み、回路が大幅に浅くなる。

### 予算とガードレール

月次AWS Budgetは **100 USD**。QPU候補はGarnet / Emerald / IBEX-Q1に固定する。
Braket Spending Limitは全機0 USDで作成し、実験時だけTerraformで配分する。3機合計が300 USDを
超える設定はapplyを失敗させる（[ADR-0003](docs/adr/0003-reference-circuit-and-cost-guardrails.md)）。

現在のIAMガードレールは拒否リスト方式で実装済み。Phase 3では3機以外を拒否する方式へ改訂し、
IAM Policy Simulatorで実測してから適用する。設計根拠と検証手順は
[`docs/03-execution-gate.md`](docs/03-execution-gate.md)と[#3](https://github.com/s-sasaki-earthsea-wizard/shor-braket/issues/3)。

> **ショット数を制限する IAM 条件キーは存在しない。**
> QPU費用はクライアント側の確認とBraket Spending Limit、その他のAWS費用はAWS Budgetsで守る。

---

## 参考文献

- P. W. Shor, "Polynomial-Time Algorithms for Prime Factorization and Discrete Logarithms on a Quantum Computer", SIAM J. Comput. 26(5), 1997
- J. A. Smolin, G. Smith, A. Vargo, "Oversimplifying quantum factoring", Nature 499, 163–165, 2013
- S. Beauregard, "Circuit for Shor's algorithm using 2n+3 qubits", quant-ph/0205095, 2002
- [Amazon Braket Developer Guide](https://docs.aws.amazon.com/braket/)

---

## ライセンス

[Apache License 2.0](LICENSE)

```
Copyright 2026 Syota Sasaki (Earthsea Wizard)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

ソースファイルにはライセンスヘッダを付ける（Phase 1 で導入）。
