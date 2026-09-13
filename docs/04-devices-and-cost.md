# Amazon Braket デバイスと課金

**取得日: 2026-09-07**（`aws braket get-device` / `search-devices` の実測値）

Braket のデバイスラインナップは頻繁に入れ替わる。実際、かつて主力だった
**IonQ Aria-1 / Aria-2 / Harmony、Rigetti Ankaa-2 / Ankaa-3、Amazon TN1 はすべて RETIRED**
になっている。実行前に必ず再取得すること。

```bash
make devices                    # リージョン横断でデバイス一覧
aws braket get-device --device-arn <arn> --region <region>
```

価格の一次情報: https://aws.amazon.com/braket/pricing/

---

## 1. 課金モデル

| 種別 | 課金構造 |
|---|---|
| ローカルシミュレータ | **無料**。AWS へのリクエストが発生しない |
| オンデマンドシミュレータ (SV1/DM1) | 実行時間あたり **$0.075/分** |
| QPU（実機） | **タスクあたり定額** + **ショットあたり従量** |
| S3 保存 | 結果オブジェクトのストレージ料金（微小） |

> ⚠️ **`get-device` の `deviceCost` にはショット単価しか含まれない。**
> タスクあたりの定額（記憶では $0.30/task）は API から取得できないため、
> 価格ページで確認し、設定ファイルに定数として持つ必要がある。
> コスト推定器を実装するときの落とし穴。

---

## 2. 実測: ONLINE のデバイス一覧（2026-09-07）

### ゲート型 QPU

| 論理名 | デバイス | 方式 | qubit | 全結合 | ショット単価 | ショット範囲 | リージョン |
|---|---|---|---|---|---|---|---|
| `cepheus` | Rigetti Cepheus-1-108Q | 超伝導 | 107 | ✗ | **$0.000425** | 10–50,000 | us-west-1 |
| `garnet` | IQM Garnet | 超伝導 | 20 | ✗ | **$0.00145** | 1–20,000 | eu-north-1 |
| `emerald` | IQM Emerald | 超伝導 | 54 | ✗ | **$0.0016** | 1–20,000 | eu-north-1 |
| `ibex` | AQT IBEX Q1 | イオントラップ | 12 | ✓ | **$0.0235** | 1–2,000 | eu-north-1 |
| `forte-ent` | IonQ Forte Enterprise 1 | イオントラップ | 36 | ✓ | **$0.08** | 100–5,000 | us-east-1 |

### シミュレータ

| 論理名 | デバイス | qubit | 価格 | ショット範囲 | リージョン |
|---|---|---|---|---|---|
| `sv1` | Amazon SV1（状態ベクトル） | 34 | $0.075/分 | 0–100,000 | eu-west-2, us-east-1, us-west-1, us-west-2 |
| `dm1` | Amazon DM1（密度行列・ノイズ可） | 17 | $0.075/分 | 0–100,000 | eu-west-2, us-east-1, us-west-1, us-west-2 |

> ⚠️ **SV1 / DM1 は eu-north-1 に存在しない**（2026-09-13 実測）。
> eu-north-1 にあるのは QPU 3 機（IQM Garnet / Emerald、AQT IBEX Q1）のみ。
> Braket はタスクを投入したリージョンの S3 バケットに結果を書くため、
> **QPU 用（eu-north-1）とシミュレータ用のバケットが 2 つ必要**になる。
> シミュレータ側は **eu-west-2（ロンドン）** を採る。EU 内で eu-north-1 に最も近く、
> 結果データが EU を出ない。

### リージョンの分布（2026-09-13 実測）

| リージョン | ONLINE のデバイス |
|---|---|
| `eu-north-1` | AQT IBEX Q1、IQM Garnet、IQM Emerald（**シミュレータなし**） |
| `eu-west-2` | SV1、DM1 のみ |
| `us-east-1` | SV1、DM1、QuEra Aquila、IonQ Forte Enterprise 1 |
| `us-west-1` | Rigetti Cepheus-1-108Q、SV1、DM1 |

**本プロジェクトの選択**: QPU は `eu-north-1`（採用した 3 機が全てここにある）、
シミュレータは `eu-west-2`。S3 バケットは両リージョンに 1 つずつ置く。

### 対象外

- **QuEra Aquila** (us-east-1, ONLINE) — アナログハミルトニアンシミュレーション (AHS) 専用。
  ゲート型回路を実行できないため本プロジェクトでは使えない
- **RETIRED**: IonQ Aria-1 / Aria-2 / Harmony、IonQ Forte-1 (OFFLINE)、
  Rigetti Aspen 系 / Ankaa-2 / Ankaa-3、Xanadu Borealis、Amazon TN1

---

## 3. 1000 ショットあたりのコスト比較

タスク定額を $0.30 と仮定した概算。

| デバイス | ショット分 | 合計（概算） | 最安との比 |
|---|---|---|---|
| Rigetti Cepheus-1-108Q | $0.43 | **約 $0.73** | 1.0× |
| IQM Garnet | $1.45 | 約 $1.75 | 2.4× |
| IQM Emerald | $1.60 | 約 $1.90 | 2.6× |
| AQT IBEX Q1 | $23.50 | 約 $23.80 | 33× |
| IonQ Forte Enterprise 1 | $80.00 | **約 $80.30** | **110×** |

> **単価差が 188 倍（$0.000425 vs $0.08）ある。**
> デバイス選択がコストを支配する。デバイス名の打ち間違いが二桁の課金差になるため、
> 実行ゲート L3（論理名 → ARN のマップ）と L4（IAM の ARN 限定）は必須。

---

## 4. デバイス選択の考え方

N = 6 の回路は 4〜6 qubit。**量子ビット数はどのデバイスでも余る**。効くのは以下 3 点。

### 4.1 接続性 — 全結合か格子か

制御モジュラー乗算は多重制御ゲートを含み、SWAP 挿入回数が接続性に強く依存する。

- **全結合**: AQT IBEX Q1、IonQ Forte Enterprise 1 → SWAP 不要、回路が浅い
- **格子（非全結合）**: Rigetti Cepheus、IQM Garnet / Emerald → SWAP が入り実効深さが増える

ただし 4〜6 qubit という小ささなら格子でも SWAP のオーバーヘッドは限定的。
**トランスパイル後の 2 qubit ゲート数を実測して比較すること**（実行ゲート A5 の副産物）。

### 4.2 ミッドサーキット測定とフィードフォワード ← 重要な発見

**IQM Garnet / Emerald のネイティブゲートセットに `cc_prx`（classically-controlled PRX）と
`measure_ff`（feed-forward measurement）が含まれている。**

```
Garnet / Emerald : ['cz', 'prx', 'cc_prx', 'measure_ff', 'barrier']
Cepheus          : ['rx', 'rz', 'cz', 'barrier']              # feed-forward なし
IBEX Q1          : ['prx', 'xx', 'rz']                        # feed-forward なし
Forte Enterprise : ['GPI', 'GPI2', 'ZZ']                      # feed-forward なし
```

これは **反復的量子位相推定（Kitaev 型 / semiclassical QFT）が実装できる**ことを意味する。
カウントレジスタを 1 qubit に畳み込み、ミッドサーキット測定と古典条件付き位相補正で
t ビット分の位相を逐次取得する方式。

N = 6 なら **作業レジスタ 3 qubit + カウント 1 qubit = 合計 4 qubit** で、
t を増やしても qubit 数が増えない。逆 QFT のマルチ制御位相ゲートも不要になり、
**回路が大幅に浅くなる**。

→ **ノイズ耐性の観点で IQM Garnet が第一候補**。ただし通常 QPE との比較実験も価値があるため、
両方を実装して同一デバイスで比較するのが望ましい。

### 4.3 2 qubit ゲート忠実度と実行ウィンドウ

累積忠実度は $\prod(\text{2q gate fidelity})$ で効く。2 qubit ゲート忠実度 99%、
トランスパイル後の 2 qubit ゲート数 60 なら $0.99^{60} \approx 0.55$。
**t を小さく取ることが実機での成否を分ける。**

校正値は実行時に取得できる:

```python
device.properties.provider   # デバイス固有の校正データ
```

投入前に必ず記録し、結果の解釈に使うこと。

**実行ウィンドウ**（UTC）も要注意。24/7 ではないデバイスがある。

| デバイス | 可用時間帯 |
|---|---|
| IonQ Forte Enterprise 1 | 毎日 24 時間 |
| Rigetti Cepheus-1-108Q | 毎日（保守時間帯を除く複数ウィンドウ） |
| IQM Garnet / Emerald | 平日中心の複数ウィンドウ |
| AQT IBEX Q1 | **曜日ごとに数時間のみ**（月 11:00–15:00 等） |
| SV1 / DM1 | 毎日 24 時間 |

時間外に投入したタスクはキューで待つ。急ぐ場合は `get-device` の
`executionWindows` を確認してから投げること。

---

## 5. 推奨する実行段階

```
1. LocalSimulator   無料      アルゴリズムの正しさ（実行ゲート A1–A3）
2. DM1              $0.075/分  ノイズモデル込みの耐性評価（任意）
3. SV1              $0.075/分  Braket マネージド環境での動作確認（A4–A5）
4. QPU              $0.73〜    実機実行
```

**SV1 を必ず挟むこと。** 「ローカルでは動くが Braket 上で落ちる」類の問題を
1 ドル未満で潰せる。DM1 を挟むかは未決（`docs/03-execution-gate.md` の未決事項参照）。

---

## 6. コスト暴走を止める仕組み

| 手段 | 定義場所 | 効果 |
|---|---|---|
| AWS Budgets（月次閾値 + SNS） | Terraform | 事後通知。止められないが気づける |
| IAM でデバイス ARN を限定 | Terraform | 高単価機種の誤用を事前に防ぐ |
| クライアントの `--max-cost` | Python | 桁間違いを事前に防ぐ |
| 対話確認プロンプト | Python | 誤操作を防ぐ |

**AWS Budgets は通知であって遮断ではない。** Budgets Actions で IAM ポリシーを
自動アタッチして遮断する構成も可能だが、本プロジェクトの規模では過剰。
まずは通知 + IAM の ARN 限定で運用する。

---

## 7. 初回セットアップの注意

- Braket はアカウントごとに**コンソールでの有効化（利用規約への同意）が必要**。
  Terraform では実施できないため手動手順として記録する
- 結果保存用 S3 バケットは **`amazon-braket-` プレフィクスを推奨**。
  AWS 管理ポリシー `AmazonBraketFullAccess` がこのプレフィクスを前提としている
- **root アカウントのアクセスキーで実行しない。** Terraform で専用 IAM ロールを作る

---

## 8. 未決事項

- [x] ~~第一候補デバイスの確定~~ → **IQM Garnet**（feed-forward 対応 + 低単価）を推奨。
      コスト最優先なら Rigetti Cepheus、回路深さ最優先なら AQT IBEX Q1
- [ ] タスク定額（$0.30/task と推定）の一次情報での確認
- [ ] 月次予算の閾値をいくらに設定するか
- [ ] Garnet を使う場合、S3 バケットも eu-north-1 に置くか（クロスリージョン転送料の回避）
- [ ] 反復的 QPE（feed-forward）を実装するか、通常 QPE のみにするか
- [ ] ノイズあり検証に DM1 を挟むか
