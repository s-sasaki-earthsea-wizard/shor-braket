# ADR-0002: AWS 実行権限とコストガードレールの設計

- **状態**: 採用
- **日付**: 2026-09-07
- **決定者**: Syota Sasaki

## 背景

Amazon Braket の QPU は「タスクあたり定額 + ショットあたり従量」で課金され、
デバイス間のショット単価差が **188 倍**（$0.000425 〜 $0.08）ある。
IonQ Forte Enterprise 1 は 1000 ショット 1 回で約 $80.30。

デバイス名の打ち間違いや、読み取り作業のつもりでの誤投入が、そのまま二桁の課金差になる。
クライアント側の実行ゲート（`docs/03-execution-gate.md` の L1–L3）は SDK を直接叩けば
素通りするため、**認証・認可レイヤでの防御が必要**。

調査の結果、当初の設計前提が誤っていたことも判明した。

## 検討した選択肢と調査結果

### デバイス制限: Allow で絞る（当初案） — **不可能**

**Braket が持つ IAM リソースタイプは `quantum-task` ただ一つ**
（`arn:${Partition}:braket:${Region}:${Account}:quantum-task/${RandomId}`）。
デバイスは「サービスリソースであって顧客リソースではない」ため、
`Allow` の `Resource` にデバイス ARN を並べる方式は AWS の想定外で、公式の例も存在しない。

AWS が文書化しているデバイス制限は **`Deny` にデバイス ARN を指定する方式のみ**。
`braket:CreateQuantumTask` / `braket:CreateJob` / `braket:GetDevice` の 3 アクションに限る。

### デバイス制限: Deny の許可リスト (`NotResource`)

理想は許可リストだが、IAM は Deny を Allow で打ち消せないため
`Deny` + `NotResource` で書くしかない。これは文書化されていない書き方であり、
`CreateQuantumTask` が `quantum-task` リソースに対しても評価される場合、
意図せず全拒否になるリスクがある。

### デバイス制限: Deny の拒否リスト

AWS が文書化している唯一の方式。「知らないデバイスは通ってしまう」弱点があるが、
挙動が保証される。

### 権限分離: 単一プロファイル vs 2 プロファイル

単一プロファイルだと、デバイス一覧の確認中にうっかり `submit` を叩けてしまう。
読み取りと実行を分ければ、認証レイヤで誤投入を止められる。

## 決定

### 1. デバイス制限は Deny の拒否リストで実装する

`infra/iam/shor-braket-guardrail-policy.json`。当面は許可リスト方式を採用しない。

拒否対象と根拠（月次予算 100 USD に対する 1000 ショット 1 回のコスト）:

| デバイス | 1 回 | 予算での回数 | 判定 |
|---|---|---|---|
| Rigetti Cepheus-1-108Q | $0.73 | 約 137 回 | 許可 |
| IQM Garnet | $1.75 | 約 57 回 | 許可 |
| IQM Emerald | $1.90 | 約 52 回 | 許可 |
| AQT IBEX Q1 | $23.80 | 約 4 回 | 拒否 → **タグゲートに改訂**（下記 2026-09-13） |
| IonQ Forte Enterprise 1 | $80.30 | **約 1 回** | 拒否 |

Hybrid Jobs は SageMaker インスタンスを時間課金で起動するため全面禁止。

許可リスト方式（`*-allowlist-EXPERIMENTAL.json`）は
`aws iam simulate-principal-policy` で期待通りに動くことを確認できてから切り替える。

**2026-09-13 改訂**: 拒否リストの判断基準を「1,000 ショットのコスト」から
「**デバイスのショット上限での最悪 1 タスクが予算に対してどれだけか**」に改める。
IonQ Forte Enterprise 1 は最悪 $400.30（予算の 4 倍）で無条件 Deny を維持する。
AQT IBEX Q1 は最悪 $47.30（予算の半分）で許可済みの超伝導機と同じ桁であり、
デバイス比較に必要なため、**リクエストタグ `campaign=device-comparison` が付いた投入だけ通す
タグゲート付き Deny** に変更する（`infra/iam/README.md` §6.1）。事故は IAM で止まり、
意図した投入は監査可能なタグ付きで通る。タグはコスト配分にも使う。
`simulate-principal-policy` で 3 ケースの検証が通らなければ AQT を Deny から外し、
クライアント側の `BRAKET_MAX_COST_USD` に任せる。

### 2. AWS プロファイルを読み取り / 実行の 2 つに分け、実行側に MFA を必須とする

| プロファイル | 権限 | MFA | 用途 |
|---|---|---|---|
| `shor-braket-ro` | 読み取りのみ | 不要 | デバイス一覧・価格取得・結果閲覧 |
| `shor-braket-exec` | タスク投入 | **必須** | `submit-sv1` / `submit-qpu` |

IAM ユーザーに読み取りポリシーを直接アタッチし、実行権限は
MFA 必須の信頼ポリシーを持つロール `ShorBraketExecutionRole` に assume して得る。

MFA 判定には **`BoolIfExists`** を使う。長期アクセスキーで署名したリクエストには
`aws:MultiFactorAuthPresent` キーが存在せず、`Bool` では Deny が発動しないため。

**2026-09-13 改訂**: 監視専用の IAM ユーザー `shor-braket-monitor` を追加し、
監視 / 操作 / 実行ロールの 3 プリンシパル構成にする。readonly ポリシーから `sts:AssumeRole` を
分離して `shor-braket-assume-exec-policy.json` とし、操作者ユーザー `shor-braket-operator` に
のみアタッチする。監視ユーザーは MFA の有無にかかわらず実行できない。
あわせて、ユーザー側の AssumeRole 許可から MFA 条件を外す。MFA は信頼ポリシー側で強制する。
長期キーのリクエストには条件キーが存在せず、ユーザー側の条件は効かないか assume を壊すため。
監視ユーザーが月次の実消費を見られるよう、readonly に `ce:GetCostAndUsage` を追加する
（1 リクエスト 0.01 USD）。

### 3. Terraform ステートはローカル管理

`backend.tf` を置かない。単独開発でロック競合が起きず、管理対象が
S3 / IAM / Budgets / SNS のみで再作成コストが低い。リモートステート用のバックエンド自体を
作る手間（鶏と卵）も避けられる。

### 4. 月次予算 100 USD

AWS Budgets で 50% / 80% / 100% / 予測 100% を SNS に通知。
クライアント側の `--max-cost` 既定値は 10 USD/回。

### 5. 秘密情報は `.env` / `terraform.tfvars` に置く

アカウント ID・バケット名・通知先メールアドレスをリポジトリに書かない。
`infra/iam/*.json` はプレースホルダのみを持ち、`make iam-render` が `.env` から値を埋めて
gitignore 対象の `rendered/` に出力する。

## 結果

- **IAM で防げないものが残る**: ショット数を制限する条件キーが存在しないため、
  桁間違いはクライアント側の `--max-cost` と AWS Budgets でしか止められない。
  AWS Budgets は通知であって遮断ではない
- **拒否リストの保守が必要**: AWS が新しい高額プロバイダを追加した場合、
  ポリシーを更新するまで素通りする。`make devices` でデバイス一覧を定期的に確認すること
- **ステートファイルを失うと `terraform import` が必要**。
  `terraform.tfstate` はローカルバックアップの対象に含めること。
  ステートには IAM ポリシー本文が平文で入るため、誤コミットに注意
- 実行時に MFA コードの入力が毎回（セッション 1 時間ごとに）必要になる。
  これは意図した摩擦であり、利便性を落としてでも誤課金を防ぐ判断

## 前提

**この ADR の内容は Phase 3 以降で実装する。優先度は低い。**
まず Phase 1–2（ローカルシミュレータ）を動かす。ローカルシミュレータは無料で
AWS 認証も不要なため、ここまでは本 ADR の設定は一切必要ない。
