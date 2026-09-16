# 実行ゲート仕様

**要件**: QPU 互換回路を対象機の `LocalEmulator` まで通した計算のみ、Amazon Braket の実機 QPU で
実行できる。密行列を使う `local-reference` 回路は検証基準であり、QPU 投入資格を発行しない。

---

## 1. 脅威モデル

守りたいのは「意図しない課金」と「検証されていない回路の実機投入」。具体的な失敗シナリオ:

| # | シナリオ | 影響 |
|---|---|---|
| F1 | 未検証の回路をそのまま QPU に投げる | 意味のない結果に課金 |
| F2 | ショット数の桁を間違える（1000 → 10000） | 課金 10 倍 |
| F3 | デバイス ARN を間違えて単価の高い機種を選ぶ | 課金が 20 倍以上に |
| F4 | 検証後に回路を編集し、検証済みのつもりで投入 | 検証の意味が消失 |
| F5 | スクリプトのループでタスクを連続投入 | 課金の暴走 |
| F6 | 認証情報の権限が広すぎて Braket 以外のリソースにも影響 | 想定外の課金・破壊 |

---

## 2. 多層防御

| 層 | 手段 | 対応する失敗 |
|---|---|---|
| L1 クライアント | validated レコード必須 + 回路ハッシュ一致 | F1, F4 |
| L2 クライアント | ショット数上限 + 推定コストの事前表示と対話確認 | F2, F5 |
| L3 クライアント | デバイスは論理名（`garnet` 等）で指定し、ARN は設定ファイルから解決 | F3 |
| L4 IAM | 候補3機以外のQPUに **Deny**。AQT は専用ロールに分離（§6、ADR-0004） | F3, F6 |
| L5 Braket Spending Limit (TF) | 3 機の合計上限 300 USD、初期値 0 USD | F2, F5 |
| L6 AWS Budgets (TF) | 月次予算 100 USD + SNS 通知（§8） | F2, F5 |

**L1〜L3 はクライアント側のため回避可能**（ユーザーが SDK を直接叩けば素通りする）。
L4〜L6 の AWS 側の防御を必ず併用すること。クライアント側のゲートは「事故防止」であって
「セキュリティ境界」ではない。

---

## 3. 回路ハッシュ

### 3.1 対象

**Braket の Circuit を正規化した中間表現に対して SHA-256 を取る。**

OpenQASM 3 のテキストを直接ハッシュしない。理由:

- 空白・改行・コメントの差で hash が変わる
- SDK バージョン間で出力フォーマットが変わる
- 命令の順序が意味的に等価でも異なる文字列になりうる

### 3.2 正規化の内容（2026-09-14 実装確定、`gate/circuit_hash.py`）

1. Circuit の instruction 列を `{op, targets, params, controls, control_state, power}` の
   辞書列に変換し、`canonical_form_version` を添えて JSON 化してから SHA-256 を取る
2. **target の順序は保存する。** この節の初稿は `sorted(targets)` と書いていたが、それをやると
   `cnot(0, 1)` と `cnot(1, 0)` が同じハッシュになる。制御と標的の入れ替わった別回路が
   validated レコードを共有してしまうので、順序は意味として扱う
3. 角度パラメータは 12 桁に丸める（浮動小数の表現差を吸収。`-0.0` は `0.0` に畳む）。
   整数パラメータ（`measure_ff` / `cc_prx` のフィードバックキー）は整数のまま残す
4. コンパイラディレクティブ（verbatim box）も含める。verbatim の有無は別の回路である
5. 物理 qubit 番号を含める。配置が変われば実機で走る回路が変わる
6. Result type（`Probability`, `Sample` 等）も含める
7. **ショット数は含めない** — 同一回路を異なるショット数で実行できるようにするため。
   ショット数は別途 L2 で制御する

正規化の規則を変えたら `CANONICAL_FORM_VERSION` を上げる。既存レコードは投入前検査で弾かれる。

### 3.3 ハッシュに含めるメタデータ

`circuit_hash` とは別に、レコードには以下を平文で保持する（`gate/record.py`）。

```json
{
  "schema_version": 1,
  "circuit_hash": "sha256:...",
  "issued_at": "...", "expires_at": "...",
  "device":  { "key": "garnet", "arn": "...", "name": "IQM Garnet" },
  "snapshot": { "capabilities_sha256": "sha256:...", "calibration_updated_at": "...",
                "fetched_at": "..." },
  "problem": { "modulus": 15, "base": 7, "count_qubits": 2, "work_qubits": 4,
               "n_specific_decomposition": true },
  "oracle_mode": "generic-constant",
  "circuit": { "canonical_form_version": 1, "physical_qubits": [...], "roles": {...},
               "swap_count": 14, "error_budget": 0.74, "native_two_qubit": 88 },
  "emulation": { "shots": 20000, "validation": {...}, "verdict": {...}, "metrics": {...} },
  "environment": { "amazon_braket_sdk": "1.127.0", ... },
  "artifact": "runs/raw/n15-emulation-.../result.json",
  "git_commit": "..."
}
```

`oracle_mode` を必ず残すのは、手掛かりの量が記録から落ちないようにするため（CLAUDE.md の前提 4）。
タグ集合（`project` / `campaign`）は未決なのでまだ入れていない。

---

## 4. validated レコード

### 4.1 発行条件

ローカルシミュレータ実行が **すべてのアサーションを通過** した場合のみ発行する。

| アサーション | 内容 |
|---|---|
| A1 | count + work レジスタの**同時分布**が理想分布と一致（厳密 TVD < 1e−9、かつ標本 TVD ≤ 1.5 × Σ√(p(1−p)/(2πn))） |
| A2 | 測定結果から連分数展開で復元した位数が古典参照実装の真値と一致 |
| A3 | 復元した位数から導いた因数が N を割り切る |
| A4 | 使用量子ビット数が対象デバイスの上限以下 |
| A5 | 回路が対象デバイスのゲートセットにトランスパイル可能 |

**A2 について**: N = 6 では位数 r = 2 が正しく復元されることのみを検証する。
「因数分解の成功」は `docs/01-why-n6-is-degenerate.md` の通り量子部分に依存しないため、
A3 の成否を N = 6 の量子部分の評価指標として使わない。

**エミュレータ / 実機の判定（2026-09-14 決定、issue #7）**: 校正ノイズ付き `LocalEmulator` の厳密分布から出した
信号残存率 λ = 1 − TVD / TVD(理想, 一様) が **0.5 以上**なら合格。誤り予算 B と exp(−B) は目安として表示するだけで判定には
使わない。標本については、理想サポート上の質量から出した λ が標準誤差の 3 倍を超えれば「信号あり」を別に記録する。
位数復元率は t=2 では一様乱数でも 0.75 になるので合否に使わない。数値の根拠は Wiki「反復 QPE と TVD の床」。

**A3 について**: N = 15 でも A3 は必要条件にとどめる。連分数展開の保証窓は t に依存せず、
一様乱数を返すデバイスでも 1 ショットあたり約 12% で因数が得られる（ADR-0001）。
量子部分の評価は A1 で行う。

Phase 1 の行列参照回路は A1〜A3 の基準値を作るが、`qpu_eligible = false` のため validated レコードを
発行しない。QPU 互換回路では A4〜A5に加え、対象機へ変換した verbatim 回路を校正データ付き
`LocalEmulator`で実行する。エミュレーションした回路と投入回路のハッシュが一致しなければ拒否する。

`LocalEmulator` の実測（2026-09-14、Wiki「LocalEmulator で実機の手前まで」）で確定した制約:

- 回路は **verbatim box 必須**。無ければ `EmulatorValidationError` で止まる。中は対象機のネイティブゲート
  だけが通り、物理 qubit 番号と接続グラフも検査される
- SDK にトランスパイラは無い。ネイティブ分解は `quantum/native.py` に手書きし、`to_unitary` で検証する
- 密行列参照回路は verbatim の有無にかかわらず拒否される（`X` / `Unitary` が非ネイティブ）
- ノイズは 1 qubit depolarizing、readout bit-flip、2 qubit depolarizing の 3 種のみ。T1/T2、クロストーク、
  リーケージは含まれない。実機より楽観的な見積りになりうる
- 校正データは `devices/snapshots/` のスナップショットから読む（`make device-snapshot`）。結果には
  `capabilities_sha256` と `calibration_updated_at` を残し、古い校正での結果を現在値として扱わない
- `Probability(target=...)` は昇順でない target 順を守らない。昇順で取り、自分でレジスタ順に並べ替える
- 全結合機（IBEX Q1）は接続グラフが空で、`ResultTypeValidator` が `Probability` の全 qubit を拒否する。
  解析用のノイズ付き回路は `noise_model.apply` で作り、エミュレータのバリデータは verbatim 回路の検証にだけ使う

### 4.2 保存場所

```
runs/
├── validated/
│   └── <hex digest>.json       # 小さいマニフェスト。git 管理対象
└── raw/
    └── <task_id>/              # 生の測定結果。gitignore
```

ファイル名は `sha256:` を落とした 64 桁の hex。ディレクトリ走査なしで引けるようにするため。
レコードには測定データそのものを入れず、`artifact` フィールドで raw の run ディレクトリを指す。
`make validated` が一覧を出す。

validated レコードを **コミット対象にする**のは監査性のため。
「どの回路が、いつ、どの検証を通って実機に投入されたか」を git 履歴として追える。

### 4.3 レコードの失効

以下の場合、既存の validated レコードは無効とする。失効判定はレコード内のメタデータで行い、
`submit` 時に `gate/preflight.py` が検査する。

| 条件 | 検査 | 効き方 |
|---|---|---|
| 回路が編集された | `circuit_hash` が動く | レコードが見つからない（最も強い） |
| **デバイスが再校正された** | `snapshot.capabilities_sha256` の一致 | **実質の主判定**。IQM は日次で校正が変わり、古い校正での λ は今日の機械についての主張ではない |
| 対象デバイスが変わった | `device.arn` の一致 | 拒否 |
| SDK のメジャーバージョンが変わった | `environment.amazon_braket_sdk` の major | 拒否 |
| 正規化の規則が変わった | `circuit.canonical_form_version` | 拒否 |
| 発行から一定期間が経過した | `expires_at`（既定 30 日） | 拒否。上の 5 つが全部動かなかった場合の保険 |

`GetDevice` は無料でエミュレーションは秒単位なので、投入直前に校正を取り直してレコードを
再発行する運用でも負担は無い。

---

## 5. submit の動作

`make submit-qpu DEVICE=garnet ORACLE=generic-constant SHOTS=2000` の実出力（2026-09-14）:

```
[gate] device                   IQM Garnet (garnet)
[gate] circuit_hash             sha256:01ebd46b9ebab1ab...
[gate] qpu eligible circuit     OK    verbatim program with no dense matrix gates
[gate] snapshot device          OK    snapshot for garnet
[gate] validated record         OK    garnet / generic-constant, issued 2026-09-14T12:12:08Z
[gate] device match             OK    record and target are the same device
[gate] calibration current      OK    calibration 2026-09-13T16:28:09Z still matches the emulation
[gate] record fresh             OK    issued 2026-09-14T12:12:08Z, expires 2026-10-14T12:12:08Z
[gate] sdk major match          OK    1.127.0
[gate] hash form match          OK    canonical form v1
[gate] emulation verdict        OK    signal fraction 0.541 >= 0.5
[gate] shots in range           OK    2000 within [1, 20000]
[gate] cost under ceiling       OK    3.20000 USD against a ceiling of 10 USD
[gate] spending limit           FAIL  no spending limit could be read for this device ...
[cost] arn                      arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet
[cost] shots                    2000
[cost] estimated                3.20000 USD
[cost] per-task ceiling         10 USD
[cost] spending limit           unavailable (issue #3)
[gate] verdict                  REFUSED
[gate]   blocked by             spending limit: ...
```

**回路側の検査はすべて通る。** 2026-09-16 に Phase 3 の Terraform（`infra/terraform/spending_limits.tf`）が
3 機の Limit を作り、readonly / execute ポリシーが `braket:SearchSpendingLimits` を許可した（issue #3）。
**Limit は 3 機とも 0 USD** なので、ゲートは「読めない」ではなく「残額が足りない」で閉じる。
実験のたびに Terraform で配分を上げる。クライアントから実 API を読む配線は issue #17。

**読めない Spending Limit は「余裕がある」とみなさない。** 読めない場合も残額不足と同じく拒否する。
サービス側の停止機構はこのリポジトリの外にある唯一の防御なので、その不在を黙って通さない。

**確認プロンプトは必須。** `--yes` フラグでスキップできるが、その場合も
`--max-cost` の指定を必須とし、推定コストが超えたら中断する（指定が無ければ終了コード 2）。

実際のタスク作成（`AwsQuantumTask.create`）は**まだ実装しない**。クライアント側のゲートは
完成したが、その背後に立つべき AWS 側のガードレールが issue #3 で未完のため、
`runner/submit.py` の `submit()` は常に `NotImplementedError` を投げる。

---

## 6. IAM による L4 ガードレール

実際のポリシー JSON は [`infra/iam/`](../infra/iam/) にある。設計根拠は
[`infra/iam/README.md`](../infra/iam/README.md)。ここでは要点のみ。

### 6.1 デバイス制限は Allow ではなく Deny で書く

**Braket が持つ IAM リソースタイプは `quantum-task` ただ一つ**
（`arn:${Partition}:braket:${Region}:${Account}:quantum-task/${RandomId}`）。
デバイスは「サービスリソースであって顧客リソースではない」ため、
`Allow` の `Resource` にデバイス ARN を並べて許可を絞る方式は AWS の想定外で、
公式ドキュメントにも例がない。

AWS が文書化しているデバイス制限は **`Deny` にデバイス ARN を指定する方式のみ**。
制限可能なアクションは `braket:CreateQuantumTask` / `braket:CreateJob` / `braket:GetDevice`。

```json
{
  "Effect": "Deny",
  "Action": ["braket:CreateQuantumTask", "braket:CreateJob"],
  "Resource": ["arn:aws:braket:*:*:device/qpu/ionq/*"]
}
```

ポリシー中のデバイス ARN は**アカウント部を `*` にする**
（`search-devices` が返す実 ARN はアカウント部が空だが、ポリシーでは `*` を書く）。

> `braket:GetDevice` を Deny に含めないこと。含めると価格・校正データが読めなくなり、
> L2 のコスト推定が機能しなくなる。

### 6.2 AQT は専用ロールに分ける（ADR-0004）

以前はリクエストタグ `campaign=device-comparison` で AQT の Deny を開けていた。
**`aws:RequestTag` は呼び出し側が自分のリクエストに乗せる値**なので、実行ロールは自分に掛かった
Deny を自分で外せる。境界ではなく操作上の段差だった。

| プリンシパル | AQT | IQM |
|---|---|---|
| `ShorBraketExecutionRole` | **無条件 Deny** | Allow |
| `ShorBraketAqtRole` | Allow | **無条件 Deny** |

`DEVICE=ibex` の打ち間違いは通常ロールで `AccessDenied` になる。高額機に触る行為は
独立した `AssumeRole` として CloudTrail に残る。タグは認可から外れ、コスト配分専用になった。

### 6.3 対象を 3 機に固定する

QPU 候補は次の 3 機だけとする。

- `arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet`
- `arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald`
- `arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1`

IAM の実測検証を行い、他の QPU に対する `CreateQuantumTask` が明示的に拒否されることを確認する。
未知の新規プロバイダを拒否できない拒否リスト方式は廃止する。

`aws iam simulate-principal-policy`（課金なし）で効果を検証できる:

```bash
make iam-verify ACCOUNT_ID=<id> PRINCIPAL=user/shor-braket
```

### 6.3 IAM で防げないもの

| リスク | IAM | 代替 |
|---|---|---|
| 高額デバイスの使用 | ✅ Deny | — |
| Hybrid Jobs の起動 | ✅ Deny | — |
| **ショット数の桁間違い** | ❌ 条件キーが存在しない | L2 + Spending Limit |
| タスクの連続投入 | ❌ | L2 + Spending Limit |

**ショット数を制限する IAM 条件キーは存在しない。**
ショット数自体は IAM で制限できないが、QPU の費用は Spending Limit でサービス側から止める。

---

## 7. Braket Spending Limit: QPU 合計上限 300 USD

Spending Limit は QPU デバイスごとのハードストップである。残額は
`limit - current spend - queued spend` で計算され、投入タスクの概算が残額を超える場合は
`CreateQuantumTask` が拒否される。

Terraform（`infra/terraform/spending_limits.tf`、2026-09-16 実装）は次を満たす。

- Garnet / Emerald / IBEX-Q1 の全機に `awscc_braket_spending_limit` を作り、初期値は各 0 USD
- tfvars の `spending_limits`（論理名 → `limit_usd` / `start_at` / `end_at`）で配分する。
  3 機の合計が 300 USD を超えると variable validation と resource precondition の両方で止まる
- 300 USD の天井は local 定数で、入力変数にしない
- `prevent_destroy = true`。プロジェクトを畳むときだけその行を外す
- `time_period` は任意。設定するなら両端が必須（API の仕様）で、期間外の投入は拒否される。
  初回 apply では未設定にし、実験のたびに limit と期間を書き換える
- 全プリンシパル（両ユーザー・両ロール）は `SearchSpendingLimits` だけ許可され、
  Create / Update / Delete は guardrail の `DenySpendingLimitChanges` が拒否する（`infra/iam/README.md` §6.2）

API の形（botocore 1.43.93 で確認）: 金額は文字列で小数 2 桁まで（`spendingLimit` / `totalSpend` / `queuedSpend`）、
期間は `timePeriod.startAt` / `endAt`（epoch 秒、boto3 は `datetime` で返す）。`gate/spending.py` はこの形を読む。

Spending Limit は**デバイス単位**なので、各機を 300 USD にすると合計 900 USD になり得る。
必ず3機の合計値を検証する。QPU投入前のクライアントも現在値と残額を読み、ローカルの概算と
AWS側の残額の両方を表示する。

Spending Limit は S3、ノートブック、Hybrid Job の EC2 費用を対象にしない。
これらは次節の AWS Budget とクライアント側確認で扱う。

---

## 8. 予算: 月次 100 USD

月次予算を **100 USD** とする。許可デバイスでの 1000 ショット実行の消化率:

| デバイス | 1 回あたり | 100 USD での回数 |
|---|---|---|
| IQM Garnet | 約 $1.75 | 約 57 回 |
| IQM Emerald | 約 $1.90 | 約 52 回 |
| AQT IBEX-Q1 | 約 $23.80 | 約 4 回 |

クライアント側の既定値:

- `--max-cost` の既定を **10 USD/回** とし、超える場合は明示指定を要求する
- 月次累計が予算の 80% を超えたら警告、100% を超えたら `--force` なしでは投入拒否

AWS Budgets のアラートは 50% / 80% / 100% / 予測 100% の 4 段階を SNS トピック `shor-braket-budget-alerts` に
通知し、email サブスクリプションで受ける（`infra/terraform/budget.tf`）。確認メールのリンクを 1 回クリックするまで届かない。

Budget はコスト配分タグ `project=shor-braket` でフィルタする（2026-09-15）。サービス単位にしないのは、
プロジェクトが Braket 以外に S3 / CloudWatch も使うため。タグが有効化されるまで Budget は 0 を示す。

月次累計の取得元は AWS Budgets の `CalculatedSpend`（`budgets:ViewBudget`、無料）。Cost Explorer は
1 リクエスト 0.01 USD なので使わない。Budgets の値は 1 日数回の更新で実時間ではないため、
クライアントの 80% / 100% 判定は「前回更新時点の累計 + 今回の概算」で行う。

---

## 9. シミュレータとインフラの依存関係

| 実行先 | AWSリソース | 課金 | Spending Limit |
|---|---|---|---|
| `LocalSimulator` | 不要 | 無料 | 対象外 |
| `LocalEmulator` | 校正データ取得時だけ `GetDevice`。保存済みJSONなら不要 | ローカル実行は無料 | 対象外 |
| QPU | eu-north-1 のS3、IAM、Braket有効化、Spending Limit | task + shots | 対象 |

`make sim` / `make emulate-*` は Terraform を一切必要としない。QPU に投入する前には Phase 3 の
インフラ構築を終える必要がある。

**SV1 / DM1 は実行経路から外した**（ADR-0004）。SV1 は verbatim 回路を実行できないため、
validated レコードを発行した回路そのものを投げられない。AWS 経路の確認は Garnet に 10 ショット
（0.3145 USD）を投げて行う。結果バケットは eu-north-1 の 1 つだけ。

---

## 10. 未決事項

- [x] ~~TVD の閾値をいくつにするか~~ → 厳密 TVD < 1e−9、標本 TVD ≤ 1.5 × 標本床、エミュレータ / 実機は λ ≥ 0.5（2026-09-14、issue #7）
- [x] ~~深さ予算を実行ゲートに組み込むか~~ → エミュレーションの厳密 λ で判定し、誤り予算 B は目安として表示（2026-09-14、issue #7）
- [x] ~~runner が付与するタグの集合~~ → `project` と `oracle` を常時、`campaign` は実行ごとに任意。
      **認可には使わない**（2026-09-15、ADR-0004）。実装は issue #17
- [ ] validated レコードの有効期限 30 日は妥当か（実装は 30 日だが、実質の主判定は
      `capabilities_sha256` の一致になった。日数を 7 日に縮める提案は §4.3）
- [ ] `--yes` を CI から使う運用を認めるか（現時点では想定しない）
- [x] ~~実機実行結果に対する「合格/不合格」判定を設けるか~~ → λ ≥ 0.5 で合格、λ > 3 × 標準誤差で「信号あり」を別に記録（2026-09-14、issue #7）
- [ ] 月次累計の取得元（Cost Explorer API は 1 リクエスト $0.01 かかる。
      ローカルに実行履歴を持って自前集計するほうが安いかもしれない）
