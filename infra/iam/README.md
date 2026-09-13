# IAM ポリシー

Braket 実行用の IAM 設計。**root アカウントのアクセスキーで実行しないこと。**

> **優先度は低い。** まずローカルシミュレータ（Phase 1–2）を動かす。
> ここは Phase 3 以降で使うが、設計だけ先に固めてある。

Phase 3 で Terraform 化する。それまでは手動でアタッチできるよう JSON を置いてある。
Terraform 化するときは `templatefile("${path.module}/../iam/*.json", {...})` で読み込めば
二重管理を避けられる。

---

## 1. プロファイル分割の方針

権限を 2 つのプロファイルに分ける。**課金が発生する操作には MFA を必須にする。**

| プロファイル | 権限 | MFA | 用途 |
|---|---|---|---|
| `shor-braket-ro` | 読み取りのみ | 不要 | デバイス一覧・価格取得・結果閲覧・コスト確認 |
| `shor-braket-exec` | タスク投入 | **必須** | `submit-sv1` / `submit-qpu` |

実装は **IAM ユーザー + assume role** 方式。

```
IAM User: shor-braket
  └─ shor-braket-readonly-policy.json      (直接アタッチ・MFA 不要)
       │
       │  sts:AssumeRole (MFA 必須)
       ▼
IAM Role: ShorBraketExecutionRole
  ├─ execution-role-trust-policy.json      (信頼ポリシー: MFA 必須)
  ├─ shor-braket-execute-policy.json       (Allow)
  └─ shor-braket-guardrail-policy.json     (Deny)
```

`~/.aws/config`:

```ini
[profile shor-braket-ro]
region = eu-north-1
# 認証情報は ~/.aws/credentials の [shor-braket-ro] に置く

[profile shor-braket-exec]
source_profile = shor-braket-ro
role_arn       = arn:aws:iam::<ACCOUNT_ID>:role/ShorBraketExecutionRole
mfa_serial     = arn:aws:iam::<ACCOUNT_ID>:mfa/shor-braket
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
| `shor-braket-readonly-policy.json` | Allow | IAM ユーザー `shor-braket` |
| `execution-role-trust-policy.json` | 信頼ポリシー | ロール `ShorBraketExecutionRole` |
| `shor-braket-execute-policy.json` | Allow | ロール `ShorBraketExecutionRole` |
| `shor-braket-guardrail-policy.json` | Deny | ロール（およびユーザーにも推奨） |
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

---

## 6. 拒否リストの中身と根拠

月次予算 **100 USD** に対する 1 回（1000 ショット）あたりのコスト。

| デバイス | 1000 shots 概算 | 予算 100 USD での実行可能回数 | 判定 |
|---|---|---|---|
| Rigetti Cepheus-1-108Q | $0.73 | 約 137 回 | ✅ 許可 |
| IQM Garnet | $1.75 | 約 57 回 | ✅ 許可 |
| IQM Emerald | $1.90 | 約 52 回 | ✅ 許可 |
| AQT IBEX Q1 | $23.80 | 約 4 回 | ❌ 拒否 |
| IonQ Forte Enterprise 1 | $80.30 | **約 1 回** | ❌ 拒否 |

**IonQ Forte Enterprise 1 は 1 回の実行で月次予算の 8 割を消費する。**
誤操作で 2 回投げたら予算超過。拒否リストに入れる根拠として十分。

AQT IBEX Q1 は全結合という利点があるが、ショット上限 2000、実行ウィンドウが
週に数時間しかない、feed-forward 非対応と条件が悪く、$23.80/回 に見合わない。

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
IQM / Rigetti / シミュレータが `allowed`、IonQ / AQT が `explicitDeny` になれば期待通り。

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
