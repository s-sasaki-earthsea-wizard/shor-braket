# shor-braket

Shor のアルゴリズムの位数発見回路を実装する。検証の主題は **N = 15 = 3 × 5**、当初要件の **N = 6 = 2 × 3** は縮退ケースとして併設する。まずローカルシミュレータで、検証を通ったものだけを **Amazon Braket** の実機 QPU に投げる。

AWS リソースは Terraform で管理し、**「ローカルシミュレータのテストをパスした計算のみ実機実行できる」** という実行ゲートをコード側とIAM側の両方で担保する。

> **注**: サービス名は Amazon **Braket**（ブラケット記法 ⟨bra|ket⟩ に由来）。

---

## ステータス

| フェーズ | 内容 | 優先度 | 状態 |
|---|---|---|---|
| Phase 0 | プロジェクト設計・ドキュメント | — | ✅ 完了 |
| **Phase 1** | **Shor アルゴリズム実装（古典前処理 + 位数発見回路）** | **高** | ⬜ 未着手 |
| **Phase 2** | **ローカルシミュレータ検証と実行ゲート** | **高** | ⬜ 未着手 |
| Phase 3 | Terraform による AWS リソース定義 | 低 | ⬜ 未着手 |
| Phase 4 | Braket オンデマンドシミュレータ (SV1) 実行 | 低 | ⬜ 未着手 |
| Phase 5 | 実機 QPU 実行と結果分析 | 低 | ⬜ 未着手 |

**まずローカルシミュレータを動かす（Phase 1–2）。** AWS 側（Phase 3 以降）は設計だけ
先に固めてあり、着手はローカルが通ってから。ローカルシミュレータは無料で AWS 認証も不要なので、
`make setup` だけで開発を始められる。

現時点では `src/` 配下は未実装。設計ドキュメントと骨組みのみ。

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
├── Makefile
├── makefiles/                   # 分割した make ターゲット
├── docs/
│   ├── 01-why-n6-is-degenerate.md   # N=6 の数論的縮退の詳細
│   ├── 02-architecture.md            # モジュール構成と回路設計
│   ├── 03-execution-gate.md          # 実行ゲートの仕様
│   ├── 04-devices-and-cost.md        # Braket デバイスと課金
│   └── adr/                          # Architecture Decision Records
├── src/shor_braket/             # 実装（未着手）
├── tests/                       # pytest
├── infra/
│   ├── iam/                     # IAM ポリシー JSON（Deny ガードレール込み）
│   └── terraform/               # AWS リソース定義（未着手）
└── runs/
    ├── validated/               # 検証済みレコード（コミット対象）
    └── raw/                     # 生の測定結果（gitignore）
```

---

## セットアップ

### Phase 1–2（ローカルのみ・AWS 不要）

```bash
make setup     # 依存関係のインストール
make sim N=6   # ローカルシミュレータ（無料・認証不要）
```

ローカルシミュレータは AWS へのリクエストを一切発生させない。**ここまでは認証設定不要。**

### Phase 3 以降（AWS）

```bash
cp .env.example .env      # アカウント ID・バケット名・予算などはすべてここ
$EDITOR .env
```

`.env` を作れば `make` が自動で読み込む。`make help` の末尾で読み込み状態を確認できる。

**AWS プロファイルは権限で 2 つに分ける。**

| プロファイル | 権限 | MFA | 用途 |
|---|---|---|---|
| `shor-braket-ro` | 読み取りのみ | 不要 | デバイス一覧・価格取得・結果閲覧 |
| `shor-braket-exec` | タスク投入 | **必須** | `submit-sv1` / `submit-qpu` |

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
make validated     # 検証済みレコードの一覧
make iam-lint      # IAM ポリシー JSON の構文検証
make iam-render    # .env の値でプレースホルダを展開
make iam-verify    # IAM ガードレールの効果をポリシーシミュレータで検証（課金なし）
```

### 予定インタフェース（Phase 1 以降）

```bash
# 1. ローカルシミュレータで検証（無料・AWS 不要）
make sim N=15
make sim N=6

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
ONLINE のゲート型 QPU と 1000 ショットあたりの概算:

| 論理名 | デバイス | qubit | 全結合 | feed-forward | ショット単価 | 1000 shots 概算 |
|---|---|---|---|---|---|---|
| `cepheus` | Rigetti Cepheus-1-108Q | 107 | ✗ | ✗ | $0.000425 | **約 $0.73** |
| `garnet` | IQM Garnet | 20 | ✗ | **✓** | $0.00145 | 約 $1.75 |
| `emerald` | IQM Emerald | 54 | ✗ | **✓** | $0.0016 | 約 $1.90 |
| `ibex` | AQT IBEX Q1 | 12 | ✓ | ✗ | $0.0235 | 約 $23.80 |
| `forte-ent` | IonQ Forte Enterprise 1 | 36 | ✓ | ✗ | $0.08 | **約 $80.30** |

> ⚠️ **ショット単価の差は 188 倍。** デバイス名の打ち間違いが二桁の課金差になる。
> かつての主力機（IonQ Aria、Rigetti Ankaa-3 など）は**すべて RETIRED** 済み。
> 実行前に `make devices` で必ず現況を確認すること。

**第一候補は IQM Garnet**。ショット単価が低く、かつネイティブゲートに `cc_prx` / `measure_ff` を
持つため**反復的 QPE（ミッドサーキット測定 + フィードフォワード）が実装できる**。
N = 6 なら合計 4 qubit で済み、回路が大幅に浅くなる。

### 予算とガードレール

月次予算は **100 USD**。許可デバイスなら Garnet で約 57 回、Cepheus で約 137 回の実行に相当する。

**IonQ Forte Enterprise 1 は 1 回で月次予算の 8 割を消費する**ため、AQT IBEX Q1 と併せて
IAM の `Deny` で拒否する（[`infra/iam/`](infra/iam/)）。

Braket の IAM リソースタイプは `quantum-task` のみで、**デバイスは `Allow` の `Resource` で
絞れない**。AWS が文書化しているのは `Deny` にデバイス ARN を書く方式だけなので、
ガードレールは拒否リストになる。設計根拠と検証手順は
[`infra/iam/README.md`](infra/iam/README.md)。

> **ショット数を制限する IAM 条件キーは存在しない。**
> 桁間違いはクライアント側の `--max-cost`（既定 10 USD/回）と AWS Budgets でしか止められない。

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
