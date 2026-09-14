# ADR-0004: AQT を別ロールに分離し、SV1 を実行経路から外して 1 リージョンにする

- **状態**: 採用
- **日付**: 2026-09-15
- **決定者**: Syota Sasaki

独立した 2 つの決定をまとめて記録する。どちらも ADR-0002 / ADR-0003 の一部を置き換える。

---

## 決定 1: AQT のタグゲートを廃し、専用ロールに分ける

### 背景

ADR-0002 は AQT IBEX Q1 を「タグで開ける Deny」とした。実行ロールに次の Deny を付け、
リクエストタグ `campaign=device-comparison` が付いたときだけ通す設計である。

```json
{
  "Sid": "DenyAqtUnlessCampaignTagged",
  "Effect": "Deny",
  "Action": ["braket:CreateQuantumTask", "braket:CreateJob"],
  "Resource": ["arn:aws:braket:*:*:device/qpu/aqt/*"],
  "Condition": {
    "StringNotEqualsIfExists": { "aws:RequestTag/campaign": "device-comparison" }
  }
}
```

理由は「無条件 Deny では粒度が粗すぎ、無条件許可ではクライアント側のガードだけが頼りになる」
（`infra/iam/README.md` §6.1）。中間が要る、という問題設定は正しかった。

**しかし `aws:RequestTag` は呼び出し側が自分のリクエストに乗せる値である。**
実行ロールは自分でタグを付けて、自分に掛かっている Deny を解除できる。つまりこれは権限境界ではなく、
能力を常時保持したままの操作上の段差にすぎない。「意図的なタグ付け」は UX であって認可ではない。

守りたい対象は単価である。

| デバイス | 2,000 shots | 上限ショットでの最悪 1 タスク | 月次予算 100 USD 比 |
|---|---:|---:|---:|
| IQM Garnet | 3.20 USD | 29.30 USD | 29% |
| IQM Emerald | 3.50 USD | 32.30 USD | 32% |
| AQT IBEX Q1 | 47.30 USD | 47.30 USD | 47% |

IBEX のショット単価は Garnet の 16.2 倍、同じ 2,000 ショットで 14.8 倍になる。
`DEVICE=ibex` の打ち間違いが 47 USD になる状況を作りたくない。

### 検討した選択肢

#### 選択肢 A: タグゲートを維持し、実測検証する（issue #2 の当初案）

- 利点: 変更が不要。ロールが 1 つで済む
- 欠点: 呼び出し側が決める値を認可条件にしている。境界にならない
- 欠点: デバイス ARN を `Resource` に取る Deny と `aws:RequestTag` の併用は AWS の公式例になく、
  期待どおり動く保証がない。フォールバックを用意する必要がある（issue #2）

#### 選択肢 B: AQT を無条件 Deny にする

- 利点: 単純で確実
- 欠点: デバイス比較（Wiki「デバイス比較」）ができない。全結合のイオントラップは比較軸として必要

#### 選択肢 C: AQT 専用ロールに分ける（採用）

- 利点: 能力が分かれる。通常ロールはタグが何であれ AQT に到達できない
- 利点: 高額機に触る行為が CloudTrail の独立した `AssumeRole` イベントになる。監査信号が立つ
- 利点: **未検証の IAM 構文を使わずに済む。** 無条件のデバイス ARN Deny は AWS が文書化しており、
  IonQ 向けに既に使っている
- 欠点: ロールとポリシーが増える。ステートの移行が要る

### 決定

**選択肢 C。** AQT 専用ロール `ShorBraketAqtRole` を作り、`campaign` タグを認可から外す。

| プリンシパル | AQT | IQM | 付与するポリシー |
|---|---|---|---|
| `shor-braket-monitor` | Deny | 読み取りのみ | readonly, guardrail, deny-aqt |
| `shor-braket-operator` | Deny | 読み取りのみ | readonly, assume-roles, guardrail, deny-aqt |
| `ShorBraketExecutionRole` | **無条件 Deny** | Allow | execute, guardrail, deny-aqt |
| `ShorBraketAqtRole` | Allow | **無条件 Deny** | execute, guardrail, deny-iqm |

2 つのロールは互いの領域を Deny する。どちらのロールも相手の仕事を誤ってできない。

**タグと役割を分離する。ロールが能力を分け、タグは会計を束ねる。**
デバイス比較キャンペーンで Garnet と Emerald を回すときは通常ロールに `campaign` タグを付ける。
IBEX だけ別ロールを assume する。キャンペーンの費用はタグで横断的に集計できるまま、能力だけが分かれる。

タグ集合は次のとおり。条件分岐は無くなる。

| タグ | 値 | 付与 | 用途 |
|---|---|---|---|
| `project` | `shor-braket` | 常時 | コスト配分 |
| `oracle` | オラクルモード | 常時 | 監査。手掛かりの量が請求データ側にも独立して残る |
| `campaign` | 任意の文字列 | 実行ごとに任意 | コスト配分のみ。**認可には使わない** |

### 結果

- `infra/iam/shor-braket-guardrail-policy.json` から `DenyAqtUnlessCampaignTagged` を削除した。
  ガードレールは全プリンシパル共通の内容（予算破壊デバイス、MFA なしのタスク作成、Hybrid Jobs、
  ノートブック）だけになる
- `shor-braket-deny-aqt-policy.json` と `shor-braket-deny-iqm-policy.json` を追加した
- `shor-braket-assume-exec-policy.json` を `shor-braket-assume-roles-policy.json` に改名し、
  2 つのロール ARN を持たせた
- `aqt-role-trust-policy.json` を追加した。信頼ポリシーは実行ロールと同じ（operator + MFA + 1 時間）
- **issue #2 の核心だった未検証項目が消える。** 検証対象は無条件 Deny だけになり、
  `infra/iam/README.md` §6.1 のフォールバックも不要になる
- AQT には 3 層が掛かる。Spending Limit 初期値 0 USD（サービス側）、ロール境界（認可）、
  クライアント側の preflight（事故防止）
- **既存ステートの移行が要る。** ポリシーとアタッチメントを改名したので、apply 前に次を実行する

  ```bash
  terraform -chdir=infra/terraform state mv \
    aws_iam_policy.assume_exec aws_iam_policy.assume_roles
  terraform -chdir=infra/terraform state mv \
    aws_iam_user_policy_attachment.operator_assume_exec \
    aws_iam_user_policy_attachment.operator_assume_roles
  ```

  実行しない場合は削除と再作成になる。どちらでも結果は同じだが、plan の差分が読みにくくなる

この決定を覆すには、`aws:RequestTag` を認可条件に使うことが妥当だと示す必要がある。
呼び出し側が値を決められる以上、境界としては使えない。

---

## 決定 2: SV1 を実行経路から外し、eu-north-1 だけを使う

### 背景

ADR-0003 の実行順は次のとおりだった。

```
LocalSimulator → QPU 互換回路 → LocalSimulator → LocalEmulator → SV1 → QPU
```

SV1 を挟む目的は「AWS 経路（IAM / S3 / Braket API）の問題を低額で潰す」ことである。
SV1 は eu-north-1 に存在しないため、この設計は **eu-west-2 に 2 つ目の結果バケット**を要求していた。

### 決定的な事実

**SV1 は verbatim 回路を実行できない。** AWS の開発者ガイドにこうある。

> Verbatim compilation is supported on the AQT, IonQ, IQM, and Rigetti devices
> and requires the use of native gates.

SV1 はこのリストにない。したがって **validated レコードを発行したまさにその回路を SV1 に投げられない。**
verbatim box を外した別の回路になり、回路ハッシュが変わるので投入ゲートが拒否する。
ゲートを迂回する経路を足すことは禁じている（CLAUDE.md 前提 3）。

### 検討した選択肢

| 検証対象 | SV1 | Garnet 10 shots |
|---|---|---|
| CreateQuantumTask・IAM | ✅ | ✅ |
| S3 への結果書き込み | ✅ | ✅ |
| タスクのライフサイクルと結果取得 | ✅ | ✅ |
| タグの付与 | ✅ | ✅ |
| verbatim の受理 | ❌ | ✅ |
| ネイティブゲートと接続性の受理 | ❌ | ✅ |
| Spending Limit の発動 | ❌ 対象外 | ✅ |
| 実機のキューと実行ウィンドウ | ❌ | ✅ |

費用は SV1 が 0.075 USD/分・最低課金 3 秒で約 0.004 USD、Garnet 10 ショットが
0.30 + 10 × 0.00145 = **0.3145 USD**。差は約 31 セント。

さらに、**SV1 が安く捕まえる失敗は、そもそも課金されない失敗である。** IAM の拒否、
バケットの不備、Spending Limit の不足はいずれもタスクが作られる前に API エラーで落ちる。
タスクが存在しないので課金もない。0.3145 USD を実際に払うのはタスクが走った場合だけである。

SV1 に残る利点は反復の速さである。IQM は平日中心の実行ウィンドウなので、結果の取得と解析で
つまずくと次の試行まで待つ。ただしこれは、保存した結果 JSON に対して `make report` を
オフラインで書けば潰せる。解析関数（`analysis/distribution.py`）は既にある。

### 決定

**SV1 を実行経路から外す。** 実行順は次のとおりになる。

```
LocalSimulator → QPU 互換回路 → LocalSimulator → LocalEmulator → QPU (最小 shots) → QPU
```

AWS 経路の確認は Garnet に 10 ショット（0.3145 USD）を投げて行う。
**結果バケットは eu-north-1 の 1 つだけ。** eu-west-2 は使わない。

### 結果

- `infra/terraform/variables.tf` から `simulator_region` と `simulator_bucket_name` を削除した
- `.env` から `AWS_REGION_SIMULATOR` と `BRAKET_RESULTS_BUCKET_SIMULATOR` を削除した
- **issue #3 のスコープがバケット 1 つぶん減る**
- DM1 は ADR 以前から「必須にしない」（issue #7）。SV1 と合わせて、
  マネージドシミュレータはこのプロジェクトで使わない
- Braket SDK は `s3_destination_folder` を省略すると `amazon-braket-{region}-{account}` を
  暗黙に作る。このプロジェクトは S3 を Terraform 管理にする方針なので、暗黙のバケットは使わず
  常に明示する

将来 SV1 / DM1 が必要になったら、eu-west-2 のバケットと IAM をそのとき作る。
verbatim を要さない用途（論理回路の大きな t での検証など）であれば意味がある。
