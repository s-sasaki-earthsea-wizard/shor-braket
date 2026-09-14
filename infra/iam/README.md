# IAM ポリシー

Braket 実行用の IAM 設計。**root アカウントのアクセスキーで実行しないこと。**

> **優先度は低い。** まずローカルシミュレータ（Phase 1–2）を動かす。
> ここは Phase 3 以降で使うが、設計だけ先に固めてある。

Phase 3 で Terraform 化する。それまでは手動でアタッチできるよう JSON を置いてある。
Terraform 化するときは `templatefile("${path.module}/../iam/*.json", {...})` で読み込めば
二重管理を避けられる。

---

## 1. プリンシパルとプロファイルの分割方針

IAM プリンシパルを **4 つ**に分ける。**課金が発生する操作には MFA を必須にする。**

| プリンシパル | 種別 | ポリシー | MFA | 用途 |
|---|---|---|---|---|
| `shor-braket-monitor` | IAM ユーザー | readonly + deny-aqt | 不要 | **監視専用。** 閲覧のみで、ロールを assume する権限を持たない |
| `shor-braket-operator` | IAM ユーザー | readonly + assume-roles + deny-aqt | 読み取り時は不要 | 操作者の日常作業。読み取りと、2 つのロールへの assume |
| `ShorBraketExecutionRole` | IAM ロール | execute + guardrail + **deny-aqt** | **必須** | IQM へのタスク投入。operator が MFA 付きで assume |
| `ShorBraketAqtRole` | IAM ロール | execute + guardrail + **deny-iqm** | **必須** | AQT へのタスク投入**のみ**。ADR-0004 |

**AQT を別ロールに分けている理由**（ADR-0004）: IBEX の単価は Garnet の 16.2 倍で、
2,000 ショットなら 3.20 USD に対して 47.30 USD になる。以前はリクエストタグ
`campaign=device-comparison` で Deny を開ける設計だったが、**`aws:RequestTag` は呼び出し側が
自分のリクエストに乗せる値**なので、実行ロールは自分に掛かった Deny を自分で外せた。
権限境界ではなく操作上の段差にすぎない。ロールを分けると、高額機に触る行為が
CloudTrail の独立した `AssumeRole` イベントになり、通常ロールはタグが何であれ AQT に到達できない。
2 つのロールは互いの領域を Deny するので、どちらも相手の仕事を誤ってできない。

操作者の端末から見たプロファイルは従来通り 2 つ。監視ユーザーの鍵は操作者の端末には置かない。

| プロファイル | 実体 | MFA | 用途 |
|---|---|---|---|
| `shor-braket-ro` | operator の長期キー | 不要 | デバイス一覧・価格取得・結果閲覧・コスト確認 |
| `shor-braket-exec` | `shor-braket-ro` を source に実行ロールを assume | **必須** | `submit-qpu`（IQM） |
| `shor-braket-aqt` | `shor-braket-ro` を source に AQT ロールを assume | **必須** | `submit-qpu DEVICE=ibex` |
| `shor-braket-monitor` | monitor の長期キー | 不要 | ダッシュボード・別端末・別の人。**実行不可** |

実装は **IAM ユーザー + assume role** 方式。

```
IAM User: shor-braket-monitor
  └─ shor-braket-readonly-policy.json      (閲覧のみ。assume の Allow を持たない)

IAM User: shor-braket-operator
  ├─ shor-braket-readonly-policy.json      (直接アタッチ・MFA 不要)
  ├─ shor-braket-deny-aqt-policy.json      (Deny。長期キーから AQT に到達させない)
  └─ shor-braket-assume-roles-policy.json  (sts:AssumeRole のみ。MFA 条件なし)
       │
       │  sts:AssumeRole (MFA は信頼ポリシー側で強制)
       ├───────────────────────────────┐
       ▼                               ▼
IAM Role: ShorBraketExecutionRole   IAM Role: ShorBraketAqtRole
  ├─ execution-role-trust-policy      ├─ aqt-role-trust-policy
  ├─ shor-braket-execute-policy       ├─ shor-braket-execute-policy
  ├─ shor-braket-guardrail-policy     ├─ shor-braket-guardrail-policy
  └─ shor-braket-deny-aqt-policy      └─ shor-braket-deny-iqm-policy
       IQM のみ                            AQT のみ
```

**監視と操作を別ユーザーにする理由**: 同じユーザーの長期キーを監視用に渡すと、
その鍵の持ち主が MFA デバイスも持っていれば実行できてしまう。別ユーザーにすれば、
監視用の鍵は assume の Allow 自体を持たないため、MFA の有無にかかわらず実行できない。

`~/.aws/config`:

```ini
[profile shor-braket-ro]
region = eu-north-1
# 認証情報は ~/.aws/credentials の [shor-braket-ro] に置く

[profile shor-braket-exec]
source_profile = shor-braket-ro
role_arn       = arn:aws:iam::<ACCOUNT_ID>:role/ShorBraketExecutionRole
mfa_serial     = arn:aws:iam::<ACCOUNT_ID>:mfa/shor-braket-operator
region         = eu-north-1
duration_seconds = 3600
```

`shor-braket-exec` を使うと AWS CLI / SDK が MFA コードを対話的に要求する。
セッションは 1 時間で失効する。

**この分割の実効性**: 読み取りプロファイルで `submit` を叩いても `AccessDenied` になる。
つまり「うっかり課金」に対する防御が認証レイヤに入る。クライアント側のゲート（L1–L3）が
回避可能なのに対し、これは回避できない。

---

## 2. ファイル

| ファイル | 種別 | アタッチ先 |
|---|---|---|
| `shor-braket-readonly-policy.json` | Allow | ユーザー `shor-braket-monitor` と `shor-braket-operator` の両方 |
| `shor-braket-assume-roles-policy.json` | Allow | ユーザー `shor-braket-operator` **のみ** |
| `execution-role-trust-policy.json` | 信頼ポリシー | ロール `ShorBraketExecutionRole` |
| `aqt-role-trust-policy.json` | 信頼ポリシー | ロール `ShorBraketAqtRole` |
| `shor-braket-execute-policy.json` | Allow | 両ロール |
| `shor-braket-guardrail-policy.json` | Deny | 全プリンシパル共通 |
| `shor-braket-deny-aqt-policy.json` | Deny | 両ユーザーと `ShorBraketExecutionRole` |
| `shor-braket-deny-iqm-policy.json` | Deny | `ShorBraketAqtRole` **のみ** |
| `shor-braket-guardrail-allowlist-EXPERIMENTAL.json` | Deny | **未検証**。§6 参照 |

IAM では Deny が常に Allow に優先するため、ガードレールは Allow ポリシーの内容に
かかわらず有効になる。

---

## 3. プレースホルダの置換

**アカウント ID とバケット名はリポジトリに書かない。** `.env` に置いて `make` から読み込む。

| プレースホルダ | `.env` のキー |
|---|---|
| `<AWS_ACCOUNT_ID>` | `AWS_ACCOUNT_ID` |
| `<RESULTS_BUCKET>` | `BRAKET_RESULTS_BUCKET` |

```bash
cp .env.example .env      # 値を埋める。.env は gitignore 対象
make iam-render           # → infra/iam/rendered/ に出力 (gitignore 対象)
```

`make iam-render ACCOUNT_ID=... BUCKET=...` で個別に上書きもできるが、
**シェル履歴に残るので常用しないこと**。`.env` 経由を既定とする。

Terraform 側の対応する変数は `infra/terraform/terraform.tfvars`
（テンプレート: `terraform.tfvars.example`）。こちらも gitignore 対象。

---

## 4. MFA 強制の書き方

### 4.1 信頼ポリシー側（主防御）

`execution-role-trust-policy.json` でロールの assume 自体に MFA を要求する。

```json
"Condition": {
  "Bool": { "aws:MultiFactorAuthPresent": "true" },
  "NumericLessThan": { "aws:MultiFactorAuthAge": "3600" }
}
```

`aws:MultiFactorAuthAge` を入れると、MFA 認証から 1 時間以上経ったセッションでは
assume できなくなる。

### 4.2 ガードレール側（保険）

`shor-braket-guardrail-policy.json` の `DenyTaskCreationWithoutMfa` で、
MFA なしの `CreateQuantumTask` を明示的に拒否する。

```json
"Condition": {
  "BoolIfExists": { "aws:MultiFactorAuthPresent": "false" }
}
```

> **`Bool` ではなく `BoolIfExists` を使うこと。**
> IAM ユーザーの長期アクセスキーで署名したリクエストには
> `aws:MultiFactorAuthPresent` キーが**存在しない**。`Bool` だと条件が一致せず
> Deny が発動しないため、長期キーが素通りする。
> `BoolIfExists` はキーが存在しない場合も真と評価するので、長期キーもまとめて拒否できる。

### 4.3 ユーザー側の AssumeRole 許可には MFA 条件を付けない

`shor-braket-assume-roles-policy.json` は `sts:AssumeRole` を無条件で Allow する。
MFA の強制は信頼ポリシー（§4.1）が担う。

ユーザー側の Allow に `aws:MultiFactorAuthPresent` 条件を付けると、長期キーで署名した
AssumeRole リクエストにはそのキーが存在しないため、条件が一致せず assume が失敗しうる。
効いたとしても信頼ポリシーと二重であり、効かなければ exec プロファイルが壊れるだけなので置かない。

---

## 5. なぜ Deny でデバイスを制限するのか

**Braket が持つ IAM リソースタイプは `quantum-task` ただ一つ。**

```
arn:${Partition}:braket:${Region}:${Account}:quantum-task/${RandomId}
```

デバイスは「サービスリソースであって顧客リソースではない」ため、
`Allow` の `Resource` にデバイス ARN を並べて許可を絞る方式は AWS の想定外であり、
公式ドキュメントにも例がない。

AWS が文書化しているデバイス制限の方法は **`Deny` にデバイス ARN を指定する方式のみ**。
制限できるアクションは以下の 3 つ。

- `braket:CreateQuantumTask` — 指定デバイスでのタスク作成を拒否
- `braket:CreateJob` — 指定デバイスでの Hybrid Job 作成を拒否
- `braket:GetDevice` — 指定デバイスの情報取得を拒否

> **`braket:GetDevice` は Deny に含めないこと。**
> 含めるとデバイスの可用性・校正データ・価格が読めなくなり、
> 実行前のコスト推定（実行ゲート L2）が機能しなくなる。

**デバイス ARN の書式（ポリシー用）**

```
arn:aws:braket:<region>:*:device/qpu/<provider>/<device_id>
arn:aws:braket:<region>:*:device/quantum-simulator/<provider>/<device_id>
```

`search-devices` が返す実 ARN はアカウント部が空（`arn:aws:braket:eu-north-1::device/...`）だが、
**ポリシーではアカウント部に `*` を書く**。ここを空のままにすると一致しない。

`braket:CreateQuantumTask` は条件キー `aws:RequestTag/<key>` と `aws:TagKeys` に対応する。
これを Deny に組み合わせると「タグを付けた意図的な投入だけ通す」ゲートが書ける（§6.1）。

---

## 6. 拒否リストの中身と根拠

判断基準は「**そのデバイスのショット上限いっぱいで 1 タスク投げたら、月次予算 100 USD に対して
どれだけ溶けるか**」。IAM はショット数を制限できないので、最悪ケースはデバイス側の上限で決まる。

| デバイス | 上限ショット | 最悪 1 タスク | 予算比 | 判定 |
|---|---|---|---|---|
| Rigetti Cepheus-1-108Q | 50,000 | $21.55 | 22% | ✅ 許可 |
| IQM Garnet | 20,000 | $29.30 | 29% | ✅ 許可 |
| IQM Emerald | 20,000 | $32.30 | 32% | ✅ 許可 |
| AQT IBEX Q1 | 2,000 | $47.30 | 47% | ⚠️ **タグゲート**（§6.1） |
| IonQ Forte Enterprise 1 | 5,000 | $400.30 | **400%** | ❌ 無条件拒否 |

**IonQ Forte Enterprise 1 は 1 タスクで予算の 4 倍を溶かせる。** これは認証レイヤで止める対象。

AQT IBEX Q1 は最悪でも予算の半分で、許可済みの超伝導機と同じ桁。一方で全結合のイオントラップとして
デバイス比較（Wiki「デバイス比較」）に必要であり、200 ショット（$5.00）運用なら予算内に収まる。
無条件 Deny では粒度が粗すぎ、無条件許可ではクライアント側のガードだけが頼りになる。
そこで **AQT を専用ロールに分ける**（ADR-0004）。

### 6.1 AQT 専用ロール

**2026-09-15 にタグゲートを廃止した。** 以前は次の Deny で、リクエストタグ
`campaign=device-comparison` が付いたときだけ AQT を通していた。

```json
{
  "Sid": "DenyAqtUnlessCampaignTagged",
  "Condition": {
    "StringNotEqualsIfExists": { "aws:RequestTag/campaign": "device-comparison" }
  }
}
```

**この設計は境界にならない。** `aws:RequestTag` は呼び出し側が自分のリクエストに乗せる値なので、
実行ロールは自分でタグを付けて、自分に掛かっている Deny を解除できる。
「意図的なタグ付け」は UX であって認可ではない。

現在は能力そのものを分けている。

| プリンシパル | AQT | IQM | 効き方 |
|---|---|---|---|
| `ShorBraketExecutionRole` | **無条件 Deny** | Allow | タグが何であれ AQT に到達できない |
| `ShorBraketAqtRole` | Allow | **無条件 Deny** | AQT 専用。assume が独立した監査イベントになる |
| 両ユーザー（長期キー） | **無条件 Deny** | 読み取りのみ | 長期キーから高額機に触れない |

利点は 3 つ。

1. **事故が権限で止まる。** `DEVICE=ibex` の打ち間違いは通常ロールでは `AccessDenied` になる。
   クライアント側のガードを通り抜けても IAM で止まる
2. **監査信号が立つ。** 高額機に触る行為が CloudTrail の独立した `AssumeRole` イベントになる。
   普通のタスクに紛れた文字列ではない
3. **未検証の構文を使わずに済む。** 無条件のデバイス ARN Deny は AWS が文書化しており、
   IonQ 向けに既に使っている。以前の「ARN を Resource に取る Deny + `aws:RequestTag`」は
   AWS の公式例になく、検証が必要だった（issue #2）

`campaign` タグは残すが、**コスト配分専用**になる。認可には使わない。
デバイス比較キャンペーンで Garnet と Emerald を回すときは通常ロールに `campaign` タグを付け、
IBEX だけ別ロールを assume する。キャンペーンの費用はタグで横断的に集計できるまま、能力だけが分かれる。
ロールが能力を分け、タグは会計を束ねる。

AQT には 3 層が掛かる。

| 層 | 手段 | 変更に要するもの |
|---|---|---|
| サービス側 | Braket Spending Limit 初期値 0 USD | Terraform + admin ロール + MFA + git の差分 |
| 認可 | `ShorBraketAqtRole` の assume | operator の MFA |
| 事故防止 | クライアントの preflight と `BRAKET_MAX_COST_USD` | なし（迂回可能） |

クライアント側の `BRAKET_MAX_COST_USD=10` なら IBEX は 412 ショットまで通り、1,000 ショット（$23.80）は
弾かれる。IAM のタグゲートとクライアントの上限は独立に効く二重の防御。

QuEra はアナログ量子シミュレーション (AHS) 専用でゲート型回路を実行できない。
Xanadu / OQC / Pasqal は現在このアカウントから見えないが、将来復活・追加された場合に
備えて先回りで拒否している。

**`DenyHybridJobsEntirely`**: Hybrid Jobs は SageMaker インスタンスを時間課金で起動するため、
コスト暴走の最大リスク。本プロジェクトでは一切使わないので全面禁止する。

### 拒否リスト方式の弱点

拒否リストは **「知らないデバイスは通ってしまう」** という本質的な弱点を持つ。
AWS が新しい高額プロバイダを追加したら、ポリシーを更新するまで素通りする。

理想は許可リストだが、IAM では Deny を Allow で打ち消せないため、
`Deny` + `NotResource` で書くしかない。それが
`shor-braket-guardrail-allowlist-EXPERIMENTAL.json`。

**このファイルは未検証。** `CreateQuantumTask` が `quantum-task` リソースに対しても
評価される場合、`NotResource` が意図せず全拒否になる可能性がある
（リストに `quantum-task/*` を含めてあるのはその対策だが、動作は未確認）。

**当面は拒否リスト方式を使うこと。** 許可リスト方式はシミュレータで
期待通りに動くことを確認できてから切り替える。

---

## 7. 検証方法（無料・課金なし）

IAM ポリシーシミュレータで評価する。実際に量子タスクを投げる必要はない。

```bash
make iam-verify     # .env の AWS_ACCOUNT_ID / IAM_PRINCIPAL を使う
```

代表的な 5 デバイスについて `EvalDecision` を並べて表示する。
実行ロールでは IQM / Rigetti が `allowed`、IonQ / AQT が `explicitDeny` になれば期待通り。
**AQT ロールを対象にすると逆になる**（AQT が `allowed`、IQM が `explicitDeny`）。
ADR-0004 でタグゲートを廃したので、タグ付きコンテキストでの評価は不要になった。

`.env` に `IAM_MONITOR_PRINCIPAL` を設定してあれば、監視ユーザーについても評価し、
MFA ありのコンテキストでも `CreateQuantumTask` と `sts:AssumeRole` が `implicitDeny` になることを確認する。
これが「監視用は実行権限を持たない」の実証。

シミュレータの呼び出し自体には `iam:SimulatePrincipalPolicy` と
`iam:GetContextKeysForPrincipalPolicy` が要る。readonly ポリシーの `IamPolicySimulation` に含めてある。

> **注意**: `simulate-principal-policy` は既定で MFA なしのコンテキストで評価する。
> MFA 必須の Deny があるため、実行ロールを対象にすると全部 `explicitDeny` になる。
> MFA ありの状態を再現するには `--context-entries
> ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean`
> を付ける。

個別に確認する場合:

```bash
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/ShorBraketExecutionRole" \
  --action-names braket:CreateQuantumTask \
  --resource-arns "arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet" \
  --context-entries ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean
```

### 7.1 AQT のタグゲートを確認する

`make iam-verify` は AQT について 3 ケースを評価する。期待値は
タグなし `explicitDeny`、`campaign=device-comparison` で `allowed`、別の値で `explicitDeny`。

リクエストタグは `--context-entries` にもう 1 つ追加して再現する。

```bash
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/ShorBraketExecutionRole" \
  --action-names braket:CreateQuantumTask \
  --resource-arns "arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1" \
  --context-entries \
    ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean \
    ContextKeyName=aws:RequestTag/campaign,ContextKeyValues=device-comparison,ContextKeyType=string
```

### 7.2 プリンシパルを作る前に検証する

`simulate-custom-policy` はポリシー本文を直接受け取るので、IAM リソースを作る前に
ロジックを検証できる。`make iam-render` の出力を渡す。

```bash
aws iam simulate-custom-policy \
  --policy-input-list \
    "$(cat infra/iam/rendered/shor-braket-execute-policy.json)" \
    "$(cat infra/iam/rendered/shor-braket-guardrail-policy.json)" \
  --action-names braket:CreateQuantumTask \
  --resource-arns "arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1" \
  --context-entries \
    ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean \
  --query 'EvaluationResults[0].EvalDecision'
```

必要な権限は `iam:SimulateCustomPolicy`。管理者プロファイルで実行する。

---

## 8. IAM で防げないこと

| リスク | IAM で防げるか | 代替手段 |
|---|---|---|
| 高額デバイスの使用 | ✅ Deny | — |
| Hybrid Jobs の起動 | ✅ Deny | — |
| MFA なしでの投入 | ✅ Deny (`BoolIfExists`) | — |
| 読み取り作業中の誤投入 | ✅ プロファイル分割 | — |
| **ショット数の桁間違い** | ❌ **条件キーが存在しない** | クライアント `--max-cost` + AWS Budgets |
| 同じタスクの連続投入 | ❌ | クライアント側 + AWS Budgets |

**ショット数を制限する IAM 条件キーは存在しない。**
`braket:CreateQuantumTask` に対して「1000 ショットまで」のような制約は表現できない。
この非対称性を理解した上で、クライアント側のコスト確認と AWS Budgets を必ず併用すること。

---

## 9. Braket の有効化（Terraform 不可）

Braket はアカウントごとにコンソールでの有効化（利用規約への同意）が必要。
これは API / Terraform から実施できないため、以下を手動で行う。

1. 管理者権限を持つプリンシパルでコンソールにサインイン
2. Amazon Braket コンソールを開き、利用規約に同意して有効化
3. サービスリンクロール `AWSServiceRoleForAmazonBraket` が作成される

有効化後に、上記の専用 IAM ユーザー / ロールで実行する。

---

## 10. その他の手動手順（Terraform 不可）

| 手順 | 理由 |
|---|---|
| **請求情報への IAM アクセスを有効化** | アカウント設定「IAM ユーザーおよびロールによる請求情報へのアクセス」。これを有効にしないと、ポリシーで許可していても IAM ユーザーから Budgets / Cost Explorer が読めない |
| **operator ユーザーに MFA デバイスを登録** | 信頼ポリシーが MFA を要求するため、未登録だと exec プロファイルが使えない。登録後の serial を `~/.aws/config` の `mfa_serial` に書く |
| Cost Explorer の有効化 | 初回はコンソールで有効化が必要。`ce:GetCostAndUsage` は 1 リクエスト 0.01 USD |
| **コスト配分タグ `project` と `campaign` の有効化** | Billing コンソールの「コスト配分タグ」。有効化しないと Cost Explorer でタグ別に絞れない。有効化後のデータにしか効かないので、最初の投入より前に行う |

monitor ユーザーには MFA を必須にしていないが、コンソールを使うなら登録を推奨する。

---

---

---

## 11. アカウントの bootstrap（一度きり・Terraform 外）: root キーの廃止と管理者の MFA 化

管理者は「IAM を作る側」なので、このプロジェクトの Terraform には入れない
（Terraform 自体が管理者権限で動くため鶏と卵になる。`terraform destroy` で管理者を消す事故も避けたい）。
代わりに手順をスクリプトとしてリポジトリに置き、値は記録しない。

### 11.1 着手前のアカウント実測（2026-09-13）

| 事実 | 影響 |
|---|---|
| root のアクセスキーが**有効**（CLI の `default` プロファイルの正体） | root は IAM で制限できない。root MFA はコンソールにしか効かず、キーは守れない。**最優先で廃止** |
| `terraform-admin` が `AdministratorAccess` + 長期キー + MFA なし | 弱点だが、**キーは作成以来一度も使われていない**（`get-access-key-last-used` が null）。コンソールログインプロファイルもなし。**廃止する** |
| root MFA は有効（仮想デバイス 1 台） | break-glass のコンソール経路は確保済み。root キーを消しても締め出されない |
| 未割り当ての仮想 MFA デバイスが 1 台 | 名前が一致すればスクリプトが再利用する |

> **範囲外の注意点**: `vecr-garage-dev-*` の 2 ユーザーは MFA なしで数か月休眠している。
> `nas-glacier-backup` も MFA なしの長期キー。本プロジェクトの対象外だが、別途棚卸しを勧める。

### 11.2 目標の形

```
IAM User: admin-base            長期キー + MFA デバイス。権限は AssumeRole 1 つだけ
  │  sts:AssumeRole (MFA 必須, 1 時間で失効)
  ▼
IAM Role: AdminRole             AdministratorAccess
```

**長期キーが漏れても、それ単体では何もできない。** ロールの assume に MFA コードが要る。
`~/.aws/config` のプロファイル `admin` がこのロールを assume し、`make tf-*` は
`AWS_PROFILE_ADMIN` としてこれを使う（呼び出し元が root なら拒否する）。

### 11.3 順番

**この順番を守ること。** 新しい経路を作って動作確認するまで、古い経路を消さない。

1. `admin-base` + `AdminRole` を作る（root キーで実行する最後の作業）
2. `admin` プロファイルで assume できることを確認する
3. `terraform-admin` を廃止する
4. root アクセスキーを削除し、`[default]` を撤去する

### 11.4 実行（手順 1〜3）

```bash
make bootstrap-admin CHECK=1     # 現状と変更予定を表示するだけ
make bootstrap-admin             # 手順 1。MFA の QR とコード入力あり
```

**使う資格は `BOOTSTRAP_PROFILE` で決まる（既定 `default`）。** Makefile は `.env` の全変数を
子プロセスに export するため、何もしないと `AWS_PROFILE=shor-braket-ro` が漏れてくる。
しかしそのプロファイルは Terraform がユーザーを作るまで存在せず、そもそも IAM を作る権限もない。
この 2 つのターゲットだけは `BOOTSTRAP_PROFILE` で資格を明示的に固定する。

```bash
make bootstrap-admin BOOTSTRAP_PROFILE=default   # bootstrap 前: 既存の管理者資格
make retire-user BOOTSTRAP_PROFILE=admin RETIRE_USER=...   # bootstrap 後
```

**このスクリプトを対話シェルに貼り付けないこと。** `set -u` は VS Code のシェル統合フック
（`__vsc_preexec: RPROMPT: parameter not set`）を壊し、`set -e` は最初の非ゼロ終了でシェルを閉じる。
`bash` に渡して実行する。

スクリプトは冪等で、既存プリンシパルの権限を 1 つも削らない。やること:

1. `admin-base` を作成し、アクセスキーを発行する。**秘密鍵は argv にもシェル履歴にも載せず**、
   `~/.aws/credentials` に直接書き込む（`aws configure set` は値が `ps` に見えるため使わない）
2. 仮想 MFA デバイスを登録する。QR の PNG はシードを含むため、終了時に消える一時ディレクトリに置く
3. `AdminRole` を作る。信頼ポリシーは MFA 必須 + `MultiFactorAuthAge < 3600`
4. `AdministratorAccess` をロールにアタッチ
5. `admin-base` にインラインポリシー `AssumeAdminRole` を付ける（`sts:AssumeRole` 1 文のみ）
6. `[profile admin]` を `~/.aws/config` に書き、assume して検証する（伝播待ちのリトライ付き）

手順 2 が通ったら、手順 3:

```bash
make retire-user RETIRE_USER=terraform-admin
```

削除前に最終使用日時を表示し、ユーザー名の入力を求める。アクセスキー、MFA、インラインポリシー、
アタッチ済みポリシー、グループ、ログインプロファイルを順に外してからユーザーを消す。

#### 中断したときの再開

スクリプトは冪等なので、そのまま `make bootstrap-admin` を再実行すれば続きから進む。
完了済みの手順は `--check` の出力で `skip` と表示される。

ひとつだけ自動で判断できないのが **MFA デバイス**。QR の PNG は終了時に消えるため、
「作成済みだが未有効化」のデバイスが残っていると、シードが認証アプリの中にあるのか、
永久に失われたのかをスクリプトからは知りようがない。この状態を検出したら質問する。

| 状況 | 答え | スクリプトの動作 |
|---|---|---|
| QR を読み取ってアプリに入っている | `y` | そのデバイスのコードを入力させる |
| 読み取る前に落ちた / アプリにない | `n` | デバイスを削除し、新しい QR を発行して読み取りを待つ |

**分からなければ `n` を選ぶこと。** 使えないデバイスを消して作り直すだけで、副作用はない。

有効化には **連続する 2 つのコード**が要る。いま表示されているコードを 1 つ目に入れ、
**表示が切り替わるのを待って**次のコードを 2 つ目に入れる。同じコードを 2 回入れると拒否される。
入力ミスは 3 回まで再試行できる。

最後の検証でも assume のために MFA コードを 1 つ求められる。作ったばかりのロールは
assume できるようになるまで数秒かかるため、スクリプトは先に 15 秒待ってから 1 回目を試みる。
それでも失敗する場合は 10 秒待って再試行するが、**再試行のたびに新しいコードが要る**。

> **注意**: この MFA プロンプトは AWS CLI が直接端末に出すため、スクリプトの伏字処理を通らない。
> **アカウント ID がそのまま表示される。** ログを貼るときは自分で伏せること。

変数で上書きできる: `BASE_USER` `ADMIN_ROLE` `PROFILE` `SRC_PROFILE` `REGION` `MFA_NAME`。
`REGION` は IAM が global のため表示上の意味しかない。Terraform は
`var.results_bucket_region` で自分のリージョンを固定する。

### 11.5 root アクセスキーの削除（手順 4）

**先に `aws sts get-caller-identity --profile admin` が通ることを確認する。**
通らないうちに消すと締め出される。root MFA は有効なのでコンソールから復旧できるが、やり直す価値はない。

```bash
# root 認証情報で実行する最後のコマンド
aws iam list-access-keys --query 'AccessKeyMetadata[].[AccessKeyId,Status]' --output text
aws iam delete-access-key --access-key-id <上で出た AccessKeyId>
```

続けて手作業で:

1. `~/.aws/credentials` と `~/.aws/config` から `[default]` を削除する。**暗黙のプロファイルを残さない。**
   以後 `--profile` なしのコマンドは失敗するが、それが狙い
2. `.env` に `AWS_PROFILE_ADMIN=admin` を設定する
3. 確認:

```bash
aws iam get-account-summary --profile admin \
  --query 'SummaryMap.{RootKeys:AccountAccessKeysPresent,RootMFA:AccountMFAEnabled}'
# 期待値: RootKeys 0, RootMFA 1
```

### 11.6 以後の運用

| 操作 | プロファイル |
|---|---|
| `make tf-plan` / `tf-apply` | `admin`（MFA を聞かれる） |
| operator / monitor のアクセスキー発行 | `admin` で `aws iam create-access-key`。Terraform には入れない（state に平文で残るため） |
| operator の MFA 登録 | operator 本人が登録する |
| 日常の読み取り・投入 | `shor-braket-ro` / `shor-braket-exec`（§1） |

root はコンソール + MFA の break-glass 専用として残す。
