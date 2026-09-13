# infra/terraform

AWS リソース定義。**IAM プリンシパルは実装済み（2026-09-13）。S3 / Budgets / SNS / CloudWatch は Phase 3 で追加する。**

---

## 管理するリソース

| リソース | 目的 | 状態 |
|---|---|---|
| IAM ユーザー `shor-braket-monitor` / `shor-braket-operator`、ロール `ShorBraketExecutionRole`、customer-managed ポリシー 4 本 | Braket 実行と監視の権限分離（[`../iam/README.md`](../iam/README.md)） | ✅ `iam.tf` |
| S3 バケット (`amazon-braket-*`) + ライフサイクル | Braket タスクの結果保存、一定期間後に Glacier へ | ⬜ Phase 3 |
| SNS トピック + AWS Budgets | **月次予算 100 USD**、50/80/100% + 予測 100% で通知 | ⬜ Phase 3 |
| CloudWatch ロググループ | 実行ログ | ⬜ Phase 3 |

ポリシー JSON は [`../iam/`](../iam/) が唯一の定義。`iam.tf` は `file()` で読み込み、
`<AWS_ACCOUNT_ID>` と `<RESULTS_BUCKET>` を `replace()` で埋める（`make iam-render` の sed と同じ置換）。
デバイスの拒否リストや AQT のタグゲートは JSON 側にあり、Terraform の変数にはしない。

## 管理しないもの

- **量子タスク** — 使い捨ての実行単位であり、Terraform の状態管理対象として不適切。SDK から投入し、結果は S3 に落とす
- **管理者プリンシパル**（`admin-base` / `AdminRole`） — Terraform 自身がこれで動くため。手順は `../iam/README.md` §11
- **アクセスキー** — state に平文で残るため。`admin` プロファイルで `aws iam create-access-key` を叩く
- **MFA デバイス** — 本人が登録する
- **Braket サービスの有効化、請求情報への IAM アクセス、コスト配分タグ** — アカウント設定。`../iam/README.md` §9–§10

---

## 使い方

前提: `../iam/README.md` §11 の bootstrap が済んでいて、`.env` に `AWS_PROFILE_ADMIN` がある。

```bash
cp terraform.tfvars.example terraform.tfvars   # 値を埋める。gitignore 対象
make tf-init
make tf-plan        # AWS_PROFILE_ADMIN で実行。MFA コードを聞かれる。root なら拒否される
make tf-apply
terraform -chdir=infra/terraform output -raw aws_config_snippet   # ~/.aws/config に貼る内容
```

`tfvars` の `aws_account_id` を設定しておくと、別アカウントのプロファイルで apply しようとしたときに
`precondition` が作成前に止める。ロール名やユーザー名を変える場合も、JSON 側の参照とずれていれば同様に止まる。

---

## ファイル構成

```
infra/terraform/
├── versions.tf              # required_version >= 1.5 / aws ~> 5.0。backend なし
├── providers.tf             # region は var.results_bucket_region。default_tags
├── variables.tf             # Phase 3 用の変数も宣言済み（budget / S3）
├── iam.tf                   # ../iam/*.json を読み込んでプリンシパルと attachment を作る
├── outputs.tf               # ロール ARN、MFA serial、~/.aws/config の雛形（sensitive）
├── terraform.tfvars.example
└── (Phase 3) s3.tf / budget.tf / logs.tf
```

`backend.tf` は置かない。**ステートはローカル管理**（決定事項、下記参照）。

---

## 設計上の注意

### ステートはローカル管理

`terraform.tfstate` をローカルに置く。S3 + DynamoDB のリモートステートは採用しない。

- 単独開発でロック競合が起きない
- 管理対象が IAM / S3 / Budgets / SNS のみで、再作成コストが低い
- リモートステート用のバックエンド自体を作る手間（鶏と卵）を避けられる

**代償**: `terraform.tfstate` は `.gitignore` 対象なので、失うと `terraform import` が必要になる。
ステートファイルは Time Machine などのローカルバックアップ対象に含めておくこと。
また **ステートには IAM ポリシー本文やアカウント ID が平文で入る**ため、誤ってコミットしないよう注意する
（`.gitignore` に `*.tfstate` / `*.tfstate.*` / `tfplan` を設定済み）。

複数人で触るようになったら S3 バックエンドに移行する。その時点で
`terraform init -migrate-state` で移行できる。

### リージョンが分かれる

Braket の QPU は機種ごとに利用可能リージョンが異なる。S3 バケットはデバイスと同じリージョンに要るため、
**主に使う QPU のリージョン（IQM / AQT なら eu-north-1）に寄せる**。別リージョンの QPU を足すなら
provider の `alias` でバケットを増やす構成にする。

### IAM でショット数は制限できない

Braket の IAM にはショット数を制限する条件キーが存在しない。
**「1000 ショットまで」のような制限は IAM では表現できない**。
コスト暴走はクライアント側のチェックと AWS Budgets でしか止められない。

### S3 バケット名のプレフィクス

AWS 管理ポリシー `AmazonBraketFullAccess` は `amazon-braket-*` プレフィクスのバケットを
前提としている。独自名にすると自前のバケットポリシーが必要になるため、
**`amazon-braket-` プレフィクスを推奨**。

---

## 未決事項

- [x] ~~ステート管理~~ → ローカル（ADR-0002）
- [x] ~~月次予算の閾値~~ → 100 USD（ADR-0002）
- [x] ~~IAM は role にするか user にするか~~ → ユーザー + MFA 必須の assume role（ADR-0002 改訂）
- [x] ~~S3 のリージョン~~ → eu-north-1（Wiki「デバイス比較」）
- [ ] ライフサイクルポリシーの期間（`results_transition_days` の既定は 90 日）
- [ ] `.terraform.lock.hcl` を gitignore から外してコミットするか（provider のハッシュを固定できる）
