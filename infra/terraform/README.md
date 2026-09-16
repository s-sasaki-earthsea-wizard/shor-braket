# infra/terraform

AWS リソース定義。**IAM プリンシパルは 2026-09-13 に初回 apply、2026-09-15 に ADR-0004 のロール分割を反映済み。
Phase 3（S3 / Budgets + SNS / Braket Spending Limit / コスト配分タグ）は 2026-09-16 に実装し、apply 待ち。**

---

## 管理するリソース

| リソース | 目的 | ファイル |
|---|---|---|
| IAM ユーザー `shor-braket-monitor` / `shor-braket-operator`、ロール `ShorBraketExecutionRole` / `ShorBraketAqtRole`、customer-managed ポリシー 6 本 | Braket 実行と監視の権限分離（[`../iam/README.md`](../iam/README.md)、ADR-0004） | `iam.tf` |
| S3 バケット（`amazon-braket-*`）+ public access block + SSE-S3 + lifecycle | Braket タスクの結果保存。**eu-north-1 の 1 つだけ**（ADR-0004）。バージョニングも Glacier 移行も無し（下記） | `s3.tf` |
| SNS トピック `shor-braket-budget-alerts` + email サブスクリプション + AWS Budgets | **月次予算 100 USD**、実績 50/80/100% + 予測 100% で通知。**コスト配分タグ `project=shor-braket` でフィルタ**（サービス単位ではない。S3 / SNS も使う） | `budget.tf` |
| Braket Spending Limit × 3 機（`awscc_braket_spending_limit`） | QPU 費用のハードストップ。初期値 0 USD、3 機合計 300 USD 以下（[`../../docs/03-execution-gate.md`](../../docs/03-execution-gate.md) §7） | `spending_limits.tf` |
| コスト配分タグ `project` / `oracle` / `campaign`（`aws_ce_cost_allocation_tag`） | Budgets のフィルタとタグ別集計。**キーは課金記録に現れてから約 24 時間後にしか有効化できない**ので変数で段階的に足す | `cost_allocation_tags.tf` |

ポリシー JSON は [`../iam/`](../iam/) が唯一の定義。`iam.tf` は `file()` で読み込み、
`<AWS_ACCOUNT_ID>` と `<RESULTS_BUCKET>` を `replace()` で埋める（`make iam-render` の sed と同じ置換）。
デバイスの拒否リストは JSON 側にあり、Terraform の変数にはしない。

`default_tags` の `project` は**小文字**。量子タスクに付くタグ（`gate/tags.py`）と同じキーにして、
コスト配分タグ 1 つでインフラとタスクの両方を束ねる。コスト配分タグのキーは大文字小文字を区別する。
awscc provider には `default_tags` が無いので、Spending Limit には同じ map を明示的に付けている。

## 管理しないもの

- **量子タスク** — 使い捨ての実行単位であり、Terraform の状態管理対象として不適切。SDK から投入し、結果は S3 に落とす
- **管理者プリンシパル**（`admin-base` / `AdminRole`） — Terraform 自身がこれで動くため。手順は `../iam/README.md` §11
- **アクセスキー** — state に平文で残るため。`admin` プロファイルで `aws iam create-access-key` を叩く（`make issue-creds`）
- **MFA デバイス** — 本人が登録する
- **CloudWatch ロググループ** — 作らない（2026-09-16 決定）。量子タスクは CloudWatch にログを書かない
  （書くのは Deny 済みの Hybrid Job だけ）。`CreateQuantumTask` の監査は CloudTrail が 90 日無料で記録する。
  execute / readonly ポリシーの `logs:` 文は残してあるが、対応するリソースは無い
- **Braket の第三者デバイス利用規約への同意** — CLI に該当コマンドが無い（aws-cli 2.34.4 で確認）。`../iam/README.md` §9
- **SNS サブスクリプションの確認** — 確認メールのリンクを 1 回クリックする。クリックするまで通知は届かない（issue #4）

---

## 使い方

前提: `../iam/README.md` §11 の bootstrap が済んでいて、`.env` に `AWS_PROFILE_ADMIN` がある。

```bash
cp terraform.tfvars.example terraform.tfvars
nano infra/terraform/terraform.tfvars          # 値を埋める。gitignore 対象
make tf-init        # provider を取得する。AWS には触らない
make tf-plan        # AWS_PROFILE_ADMIN で実行。MFA コードを聞かれる。root なら拒否される
make tf-show        # 保存済みの plan を読み直す
make tf-apply
terraform -chdir=infra/terraform output -raw aws_config_snippet   # ~/.aws/config に貼る内容 (ro / exec / aqt)
```

`tfvars` の `aws_account_id` を設定しておくと、別アカウントのプロファイルで apply しようとしたときに
`precondition` が作成前に止める。ロール名やユーザー名を変える場合も、JSON 側の参照とずれていれば同様に止まる。

**テンプレートのまま（`000000000000`）だと plan がこのガードで止まる。** 実際の値を埋めること。
`.env` の `AWS_ACCOUNT_ID` も同じ placeholder を持つので、そちらも埋める（`make iam-verify` が ARN を組むのに使う）。

**`results_bucket_name` は `amazon-braket-` で始まること**（validation で止まる）。Braket のサービスリンクロールが
結果を書けるのはこのプレフィクスのバケットだけで、別名にするとバケットポリシーが要る。`.env` の
`BRAKET_RESULTS_BUCKET` と同じ値にする（IAM ポリシーにも同じ名前が埋まる）。

### 運用: plan と apply を分ける

| 操作 | 誰が | 備考 |
|---|---|---|
| `make tf-init` | 誰でも | provider の取得だけ。認証も課金も不要 |
| `make tf-plan` | 誰でも（読み取りのみ） | admin セッションのキャッシュが生きていれば MFA を聞かれない |
| `make tf-show` | 誰でも | 保存済みの plan を読み直す。AWS に一切アクセスしない |
| `make tf-apply` / `tf-destroy` | 操作者本人 | インフラを実際に変える操作。`AdminRole` の信頼ポリシーが MFA（1 時間で失効）を要求する |

`tfplan` は plan と apply の間に固定される。plan の出力を読んでから apply すること。

**plan ファイルの置き場所は `infra/terraform/tfplan`。** `make tf-plan` は `terraform -chdir=$(TF_DIR) plan -out=tfplan`
を実行するので、`-out` のパスは chdir 先から見た相対になる。リポジトリのルートで `terraform show tfplan` を叩くと
「no such file or directory」になる。`make tf-show`、または `terraform -chdir=infra/terraform show tfplan` を使うこと。

### Spending Limit の運用: 実験ごとに tfvars を書き換える

3 機とも **0 USD** で作る。0 のあいだはサービス側が `CreateQuantumTask` をすべて拒否するので、
クライアントが何を言おうと課金は起きない。実験のときだけ `terraform.tfvars` の `spending_limits` を書き換えて
plan / apply する。

```hcl
spending_limits = {
  garnet  = { limit_usd = 5, start_at = "2026-09-17T00:00:00Z", end_at = "2026-09-24T00:00:00Z" }
  emerald = { limit_usd = 0 }
  ibex    = { limit_usd = 0 }
}
```

- 期間は `start_at` / `end_at` の**両方**を書くか、両方省略する（API の仕様）。省略なら常時有効
- 3 機の**合計**が 300 USD を超えると variable validation と resource precondition の両方で止まる。
  300 は `spending_limits.tf` の local 定数で、tfvars からは変えられない
- `prevent_destroy = true` なので `tf-destroy` は Limit で失敗する。プロジェクトを畳むときは
  `spending_limits.tf` からその 1 行を外してから destroy する
- 変更は admin ロール（MFA）+ git の差分としてしか起こせない。operator / 両ロールは `SearchSpendingLimits` だけ許可され、
  Create / Update / Delete は guardrail が Deny する（`../iam/README.md` §6.2）
- 現在値は `terraform output spending_limits`（limit / total_spend / queued_spend）か、
  `aws braket search-spending-limits --profile shor-braket-ro` で読める

### コスト配分タグ: 2 段 apply

キーは「タグ付きリソースが課金記録に載ってから約 24 時間後」にしか有効化できない。
それより前に `Active` にしようとすると apply が失敗する。

| 段 | いつ | `active_cost_allocation_tags` |
|---|---|---|
| 1 | Phase 3 の初回 apply | `[]` |
| 2 | 1 の約 24 時間後 | `["project"]`（インフラの `default_tags` から現れる）。Garnet の Limit を経路確認ぶん（5 USD）上げる apply と同時でよい |
| 3 | 最初の量子タスク（経路確認、#17）の約 24 時間後 | `["project", "oracle", "campaign"]` |

キーが現れたかは `aws ce list-cost-allocation-tags --profile admin` で確認する。
Budget の `TagKeyValue` フィルタはタグが `Active` になるまで何も数えない（0 を示す）。

### MFA と Terraform

AWS provider は **MFA コードを対話的に要求できない**。`mfa_serial` を持つプロファイルを
そのまま渡すと、こう落ちる。

```
Error: assume role with MFA enabled, but AssumeRoleTokenProvider session option not set.
```

provider に `AssumeRoleTokenProvider` を差し込む仕組みがないため。一方 **AWS CLI は MFA を聞けて、
assume したセッションをキャッシュする**。そこで `make tf-plan` などは

1. `aws configure export-credentials --profile $(TF_PROFILE) --format env` で
   プロファイルを一時資格（`AWS_ACCESS_KEY_ID` / `..._SECRET_...` / `AWS_SESSION_TOKEN`）に解決し
2. それを環境変数として Terraform に渡す。`AWS_PROFILE` は unset して provider が
   assume をやり直さないようにする

という手順を踏む。**MFA コードを聞かれるのは CLI 側**で、セッションが生きている 1 時間は聞かれない。
`aws configure export-credentials` は AWS CLI v2.12 以降が必要（実測 v2.34.4 で動作）。
awscc provider も同じ環境変数を読むので、追加の設定は要らない。

キャッシュは `~/.aws/cli/cache/` にある。端末で `aws sts get-caller-identity --profile admin` を 1 回通せば、
同じユーザーの別プロセスからも 1 時間はそのセッションが使える。

### ADR-0004 のステート移行（2026-09-15、一度きり）

ポリシーとアタッチメントの Terraform 上の名前を変えたので、旧ステートには先に `state mv` を当てる。
ポリシーの AWS 上の名前（`shor-braket-assume-exec` → `shor-braket-assume-roles`）も変わるため、
**mv してもそのポリシーは replace になる。** 実害はない（operator の assume が数秒できないだけ）。

```bash
terraform -chdir=infra/terraform state mv aws_iam_policy.assume_exec aws_iam_policy.assume_roles
terraform -chdir=infra/terraform state mv \
  aws_iam_user_policy_attachment.operator_assume_exec aws_iam_user_policy_attachment.operator_assume_roles
```

---

## ファイル構成

```
infra/terraform/
├── versions.tf              # required_version >= 1.5 / aws ~> 5.0 / awscc ~> 1.79。backend なし
├── .terraform.lock.hcl      # provider のバージョンとハッシュ。コミットする（下記）
├── providers.tf             # aws と awscc、region は var.results_bucket_region。aws は default_tags
├── variables.tf             # spending_limits の validation（キー / 小数 2 桁 / 合計 300 / 期間の両端）を含む
├── iam.tf                   # ../iam/*.json を読み込んでプリンシパルと attachment を作る
├── s3.tf                    # 結果バケット。public access block、SSE-S3、multipart 中断の掃除
├── budget.tf                # SNS topic + topic policy + email subscription + 月次 Budget
├── spending_limits.tf       # awscc_braket_spending_limit × 3。天井 300 USD の local 定数と precondition
├── cost_allocation_tags.tf  # aws_ce_cost_allocation_tag を変数で段階的に有効化
├── outputs.tf               # ロール ARN、MFA serial、バケット名、Spending Limit の現在値、~/.aws/config の雛形
└── terraform.tfvars.example
```

`backend.tf` は置かない。**ステートはローカル管理**（決定事項、下記参照）。

---

## 設計上の注意

### ステートはローカル管理

`terraform.tfstate` をローカルに置く。S3 + DynamoDB のリモートステートは採用しない。

- 単独開発でロック競合が起きない
- 管理対象が IAM / S3 / Budgets / SNS / Spending Limit のみで、再作成コストが低い
- リモートステート用のバックエンド自体を作る手間（鶏と卵）を避けられる

**代償**: `terraform.tfstate` は `.gitignore` 対象なので、失うと `terraform import` が必要になる。
ステートファイルは Time Machine などのローカルバックアップ対象に含めておくこと。
また **ステートには IAM ポリシー本文やアカウント ID が平文で入る**ため、誤ってコミットしないよう注意する
（`.gitignore` に `*.tfstate` / `*.tfstate.*` / `tfplan` を設定済み）。

複数人で触るようになったら S3 バックエンドに移行する。その時点で
`terraform init -migrate-state` で移行できる。

### `.terraform.lock.hcl` はコミットする（2026-09-16）

ロックファイルに入るのは provider の名前・バージョン・バイナリのハッシュだけで、アカウントの情報は無い。
コミットすると `terraform init` がハッシュを照合するので、差し替えられた provider バイナリを弾ける。
別の機械や後日の再 init でも同じバージョンが選ばれる。ハッシュは darwin_arm64 のぶんだけなので、
コンテナ（linux_amd64）から Terraform を回すことになったら `terraform providers lock -platform=linux_amd64` で足す。

### リージョンは 1 つ

Braket はタスクを投入したリージョンの S3 バケットに結果を書く。2026-09-13 実測のデバイス分布:

| リージョン | ONLINE のデバイス |
|---|---|
| `eu-north-1` | AQT IBEX Q1、IQM Garnet、IQM Emerald（**シミュレータなし**） |
| `eu-west-2` | SV1、DM1 のみ |
| `us-east-1` | SV1、DM1、QuEra Aquila、IonQ Forte Enterprise 1 |
| `us-west-1` | Rigetti Cepheus-1-108Q、SV1、DM1 |

採用した QPU 3 機はすべて `eu-north-1` にある。SV1 / DM1 は verbatim 回路を実行できないので
実行経路から外した（ADR-0004）。したがって**バケットは `eu-north-1` の 1 つだけ**で、provider の alias も要らない。
Spending Limit もデバイスと同じリージョンに置く必要があり、`spending_limits.tf` の precondition が確認する。
Budgets と Cost Explorer はグローバルサービスで、provider が自分で us-east-1 のエンドポイントに向ける。

### S3 に Glacier 移行を付けない（2026-09-16）

1 タスクの結果 JSON は 1 MB 以下。100 タスク溜めても Standard で月 0.003 USD 未満。Glacier は
オブジェクトごとに 40 KB のメタデータ overhead と移行リクエストの課金が乗り、小さいオブジェクトでは
逆に高くつく。実験が済んだら結果を NAS に写してからバケットを空にし、インフラごと destroy する運用。
`force_destroy` は false のままなので、中身が残っていると destroy は失敗する（消し忘れ防止）。

### IAM でショット数は制限できない

Braket の IAM にはショット数を制限する条件キーが存在しない。
**「1000 ショットまで」のような制限は IAM では表現できない**。
コスト暴走はクライアント側のチェック、Braket Spending Limit、AWS Budgets で止める。

### S3 バケット名のプレフィクス

Braket のサービスリンクロール `AWSServiceRoleForAmazonBraket` が結果を書けるのは `amazon-braket-*` の
バケットだけ。独自名にすると `braket.amazonaws.com` を許可するバケットポリシーが要る。
このプロジェクトでは **`amazon-braket-` プレフィクスを必須**にし、`variables.tf` の validation で止める。

---

## 未決事項

- [x] ~~ステート管理~~ → ローカル（ADR-0002）
- [x] ~~月次予算の閾値~~ → 100 USD（ADR-0002）
- [x] ~~IAM は role にするか user にするか~~ → ユーザー + MFA 必須の assume role（ADR-0002 改訂）
- [x] ~~S3 のリージョン~~ → eu-north-1 のみ（ADR-0004）
- [x] ~~Budget のフィルタ~~ → コスト配分タグ `project`（2026-09-15）
- [x] ~~月次累計の取得元~~ → AWS Budgets の `CalculatedSpend`（無料、`budgets:ViewBudget` で読める。Cost Explorer は使わない）（2026-09-15）
- [x] ~~ライフサイクルポリシーの期間~~ → Glacier 移行なし（2026-09-16）
- [x] ~~`.terraform.lock.hcl` をコミットするか~~ → コミットする（2026-09-16）
- [x] ~~CloudWatch ロググループ~~ → 作らない（2026-09-16）
- [x] ~~`spending_limit` の文字列表現~~ → **`tostring()` を使う**（2026-09-16 実測）。下記
- [x] ~~Budget の `TagKeyValue` フィルタをタグ有効化前に作れるか~~ → **作れる**（2026-09-16 実測）。下記

---

## 初回 apply の実測（2026-09-16）

11 リソースを作成し、IAM ポリシー 3 本を in-place 更新した。そのとき分かった API の挙動が 2 つある。

### `spending_limit` は最短表記に正規化される

`format("%.2f", …)` が作る `"0.00"` を送ると、API は `"0"` を返す。値は同じでも文字列が違うので、
**apply 直後の plan が毎回 `0 -> 0.00` の差分を出す**（実測）。`tostring()` は API と同じ最短表記
（`"0"` / `"5"` / `"5.5"` / `"5.25"`）を作るので、これに変えて差分が消えることを確認した。
`\d+(\.\d{1,2})?` のパターンも満たす。

### `time_period` を省略しても API が期間を付ける

「期間なし」は作れない。省略すると **作成時刻から 2125-12-30 まで**の期間が自動で入る。
awscc はこれを computed として受け取るので plan に差分は出ないが、`search-spending-limits` の
レスポンスには常に `timePeriod` が乗る。クライアント側（`gate/spending.py`）は期間ありを前提に読む。

### SNS の確認ページには解除リンクがある（2026-09-16 に踏んだ）

確認メールの「Confirm subscription」を開くと、**確認完了ページ自体に解除リンクが載っている**。
そこを続けて押すとサブスクリプションは `Deleted` になり、`SubscriptionsConfirmed` は 0 のままになる。
確認できたかは次で読む（`shor-braket-ro` で通る）。

```bash
topic="$(terraform -chdir=infra/terraform output -raw budget_alerts_topic_arn)"
aws sns list-subscriptions-by-topic --topic-arn "$topic" --profile shor-braket-ro \
  --query 'Subscriptions[].SubscriptionArn' --output text
```

| 表示 | 意味 |
|---|---|
| `PendingConfirmation` | メールは送られたが、まだ確認されていない |
| `arn:aws:sns:...:shor-braket-budget-alerts:<uuid>` | **確認済み。これが正常** |
| `Deleted` | 解除された。通知は届かない。作り直しが要る |

`Deleted` になったら次の apply で作り直される（Terraform が消えたサブスクリプションを検出する）。
**確認リンクを押したらそのままタブを閉じること。**

### Budget のタグフィルタはタグ有効化前でも作れる

`TagKeyValue` = `user:project$shor-braket` のフィルタは、コスト配分タグが `Active` になる前でも
そのまま作成できた。段 1 をフィルタ無しで作る回避策は要らなかった。ただし**集計されるのはタグ有効化後のデータだけ**
なので、有効化前の `CalculatedSpend` は 0 のまま。`shor-braket-ro` で `describe-budget` が通ることも確認した
（`budgets:ViewBudget`、無料）。
