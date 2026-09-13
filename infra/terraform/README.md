# infra/terraform

AWS リソース定義。**Phase 3 で実装予定。現時点では空。**

---

## 管理するリソース

| リソース | 目的 |
|---|---|
| S3 バケット (`amazon-braket-*`) | Braket タスクの結果保存 |
| S3 ライフサイクルポリシー | 一定期間後に Glacier へ移行 / 削除 |
| IAM ユーザー / ポリシー | Braket 実行用。**高額デバイスを Deny で拒否**（[`../iam/`](../iam/)） |
| SNS トピック | コスト警告の通知先 |
| AWS Budgets | **月次予算 100 USD** + SNS 通知（50/80/100% + 予測 100%） |
| CloudWatch ロググループ | 実行ログ |

ポリシー JSON は [`infra/iam/`](../iam/) に置き、Terraform からは
`file("${path.module}/../iam/shor-braket-run-policy.json")` で読み込んで二重管理を避ける。
プレースホルダは `templatefile()` で埋める。

## 管理しないもの

- **量子タスク** — 使い捨ての実行単位であり、Terraform の状態管理対象として不適切。
  SDK から投入し、結果は S3 に落とす
- **Braket サービスの有効化** — コンソールでの利用規約同意が必要で Terraform 不可。
  手動手順として記録する

---

## 想定ファイル構成

```
infra/terraform/
├── versions.tf          # required_version / required_providers
├── providers.tf         # aws provider (複数リージョン: alias)
├── variables.tf
├── terraform.tfvars.example
├── s3.tf                # 結果保存バケット + ライフサイクル
├── iam.tf               # Braket 実行ロール / ポリシー
├── budget.tf            # AWS Budgets + SNS
└── outputs.tf
```

`backend.tf` は置かない。**ステートはローカル管理**（決定事項、下記参照）。

---

## 設計上の注意

### ステートはローカル管理

`terraform.tfstate` をローカルに置く。S3 + DynamoDB のリモートステートは採用しない。

- 単独開発でロック競合が起きない
- 管理対象が S3 / IAM / Budgets / SNS のみで、再作成コストが低い
- リモートステート用のバックエンド自体を作る手間（鶏と卵）を避けられる

**代償**: `terraform.tfstate` は `.gitignore` 対象なので、失うと `terraform import` が必要になる。
ステートファイルは Time Machine などのローカルバックアップ対象に含めておくこと。
また **ステートには IAM ポリシー本文などが平文で入る**ため、誤ってコミットしないよう注意する
（`.gitignore` に `*.tfstate` / `*.tfstate.*` を設定済み）。

複数人で触るようになったら S3 バックエンドに移行する。その時点で
`terraform init -migrate-state` で移行できる。

### リージョンが分かれる

Braket の QPU は機種ごとに利用可能リージョンが異なる（IQM は eu-north-1、
IonQ は us-east-1、Rigetti は us-west-1 など）。一方 S3 バケットは 1 リージョンに置く。

**クロスリージョンのデータ転送料金が発生する可能性がある**ため、
主に使う QPU のリージョンに S3 を寄せるか、リージョンごとにバケットを作るかを決める必要がある。
provider の `alias` を使って複数リージョンを扱う構成にする。

### IAM でショット数は制限できない

Braket の IAM にはショット数を制限する条件キーが存在しない。
`braket:CreateQuantumTask` の Resource でデバイスを限定することはできるが、
**「1000 ショットまで」のような制限は IAM では表現できない**。
コスト暴走はクライアント側のチェックと AWS Budgets でしか止められない。

### S3 バケット名のプレフィクス

AWS 管理ポリシー `AmazonBraketFullAccess` は `amazon-braket-*` プレフィクスのバケットを
前提としている。独自名にすると自前のバケットポリシーが必要になるため、
**`amazon-braket-` プレフィクスを推奨**。

---

## 未決事項

- [ ] ステート管理: ローカル state か、S3 + DynamoDB のリモート state か
      （個人プロジェクトの規模ではローカルでも可。ただし多環境に広げるならリモート）
- [ ] 月次予算の閾値
- [ ] IAM は role にするか user にするか（ローカルからの実行が主なら
      IAM Identity Center / SSO + assume role が望ましい）
- [ ] S3 のリージョンをどこに置くか（QPU 選定の確定待ち）
- [ ] ライフサイクルポリシーの期間
