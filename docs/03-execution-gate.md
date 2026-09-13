# 実行ゲート仕様

**要件**: ローカルシミュレータでの検証をパスした計算のみ、Amazon Braket の課金対象デバイス
（オンデマンドシミュレータ SV1 / 実機 QPU）で実行できる。

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
| L4 IAM | `braket:CreateQuantumTask` を高額デバイス ARN に対して **Deny**（§6） | F3, F6 |
| L5 AWS Budgets (TF) | 月次予算 100 USD + SNS 通知（§7） | F2, F5 |

**L1〜L3 はクライアント側のため回避可能**（ユーザーが SDK を直接叩けば素通りする）。
L4/L5 の AWS 側の防御を必ず併用すること。クライアント側のゲートは「事故防止」であって
「セキュリティ境界」ではない。

---

## 3. 回路ハッシュ

### 3.1 対象

**Braket の Circuit を正規化した中間表現に対して SHA-256 を取る。**

OpenQASM 3 のテキストを直接ハッシュしない。理由:

- 空白・改行・コメントの差で hash が変わる
- SDK バージョン間で出力フォーマットが変わる
- 命令の順序が意味的に等価でも異なる文字列になりうる

### 3.2 正規化の内容（実装時に確定）

1. Circuit の instruction 列を `(gate_name, sorted(targets), sorted(controls), rounded(angles))` の
   タプル列に変換
2. 角度パラメータは有限桁に丸める（浮動小数の表現差を吸収）
3. Result type（`Probability`, `Sample` 等）も含める
4. **ショット数は含めない** — 同一回路を異なるショット数で実行できるようにするため。
   ショット数は別途 L2 で制御する

### 3.3 ハッシュに含めるメタデータ

`circuit_hash` とは別に、レコードには以下を平文で保持する。

```json
{
  "circuit_hash": "sha256:...",
  "problem": { "N": 6, "a": 5, "t": 2, "n_work": 3 },
  "oracle_mode": "generic-repeated",
  "tags": { "project": "shor-braket", "campaign": "" },
  "force_quantum": true,
  "sdk_version": "amazon-braket-sdk==x.y.z",
  "git_commit": "..."
}
```

---

## 4. validated レコード

### 4.1 発行条件

ローカルシミュレータ実行が **すべてのアサーションを通過** した場合のみ発行する。

| アサーション | 内容 |
|---|---|
| A1 | カウントレジスタの測定分布が理想分布と一致（TVD < 閾値） |
| A2 | 測定結果から連分数展開で復元した位数が古典参照実装の真値と一致 |
| A3 | 復元した位数から導いた因数が N を割り切る |
| A4 | 使用量子ビット数が対象デバイスの上限以下 |
| A5 | 回路が対象デバイスのゲートセットにトランスパイル可能 |

**A2 について**: N = 6 では位数 r = 2 が正しく復元されることのみを検証する。
「因数分解の成功」は `docs/01-why-n6-is-degenerate.md` の通り量子部分に依存しないため、
A3 の成否を N = 6 の量子部分の評価指標として使わない。

**A3 について**: N = 15 でも A3 は必要条件にとどめる。連分数展開の保証窓は t に依存せず、
一様乱数を返すデバイスでも 1 ショットあたり約 12% で因数が得られる（ADR-0001）。
量子部分の評価は A1 で行う。

### 4.2 保存場所

```
runs/
├── validated/
│   └── <circuit_hash>.json     # 小さいマニフェスト。git 管理対象
└── raw/
    └── <task_id>/              # 生の測定結果。gitignore
```

validated レコードを **コミット対象にする**のは監査性のため。
「どの回路が、いつ、どの検証を通って実機に投入されたか」を git 履歴として追える。

### 4.3 レコードの失効

以下の場合、既存の validated レコードは無効とする。

- SDK のメジャーバージョンが変わった
- 対象デバイスが変わった（デバイスごとにトランスパイル結果が異なるため A5 が再検証を要する）
- レコードの発行から一定期間（既定 30 日）が経過した

失効判定はレコード内のメタデータで行い、`submit` 時に検査する。

---

## 5. submit の動作

```
$ make submit-qpu N=6 DEVICE=garnet SHOTS=1000

[gate] building circuit          N=6 a=5 t=2 oracle=generic-repeated
[gate] circuit_hash              sha256:3f2a...
[gate] validated record          FOUND  (runs/validated/3f2a....json)
[gate]   simulated at            2026-09-07T12:34:56Z
[gate]   assertions              A1 ✓  A2 ✓  A3 ✓  A4 ✓  A5 ✓
[gate]   sdk version             match
[gate]   age                     0 days  (limit 30)

[cost] device                    IQM Garnet (arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet)
[cost] shots                     1000
[cost] estimated                 $X.XX   (task $A + 1000 x $B)
[cost] month-to-date             $Y.YY / budget $Z.ZZ

Proceed? [y/N]
```

**確認プロンプトは必須。** `--yes` フラグでスキップできるが、その場合も
`--max-cost` の指定を必須とし、推定コストが超えたら中断する。

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

### 6.2 これは許可リストではなく拒否リスト

IAM は Deny を Allow で打ち消せないため、許可リストは `Deny` + `NotResource` でしか
書けず、この書き方は AWS に文書化されていない。
**当面は拒否リストで運用し、新デバイス追加時にポリシー更新が必要**という弱点を受け入れる。

`aws iam simulate-principal-policy`（課金なし）で効果を検証できる:

```bash
make iam-verify ACCOUNT_ID=<id> PRINCIPAL=user/shor-braket
```

### 6.3 IAM で防げないもの

| リスク | IAM | 代替 |
|---|---|---|
| 高額デバイスの使用 | ✅ Deny | — |
| Hybrid Jobs の起動 | ✅ Deny | — |
| **ショット数の桁間違い** | ❌ 条件キーが存在しない | L2 + AWS Budgets |
| タスクの連続投入 | ❌ | L2 + AWS Budgets |

**ショット数を制限する IAM 条件キーは存在しない。**
ショット数の暴走は AWS Budgets（L5）とクライアント側（L2）でしか止められない。

---

## 7. 予算: 月次 100 USD

月次予算を **100 USD** とする。許可デバイスでの 1000 ショット実行の消化率:

| デバイス | 1 回あたり | 100 USD での回数 |
|---|---|---|
| Rigetti Cepheus-1-108Q | 約 $0.73 | 約 137 回 |
| IQM Garnet | 約 $1.75 | 約 57 回 |
| IQM Emerald | 約 $1.90 | 約 52 回 |

クライアント側の既定値:

- `--max-cost` の既定を **10 USD/回** とし、超える場合は明示指定を要求する
- 月次累計が予算の 80% を超えたら警告、100% を超えたら `--force` なしでは投入拒否

AWS Budgets のアラートは 50% / 80% / 100% / 予測 100% の 4 段階を SNS に通知する。

---

## 8. 未決事項

- [ ] TVD の閾値をいくつにするか（シミュレータは理想なので厳しくできるはず。統計誤差の扱い）
- [ ] 深さ予算（トランスパイル後 2 qubit ゲート数の上限、または推定忠実度の下限）を
      実行ゲートに組み込むか。実機ペイロードの選定に使う（ADR-0001）
- [ ] runner が付与するタグの集合を確定する。`project` は常時、`campaign` は `.env` の
      `BRAKET_CAMPAIGN` が非空のときのみ。`campaign` は AQT のタグゲートの鍵であり
      コスト配分タグでもある（`infra/iam/README.md` §6.1）
- [ ] validated レコードの有効期限 30 日は妥当か
- [ ] `--yes` を CI から使う運用を認めるか（現時点では想定しない）
- [ ] 実機実行結果に対する「合格/不合格」判定を設けるか（ノイズがあるため単純な閾値は難しい）
- [ ] 月次累計の取得元（Cost Explorer API は 1 リクエスト $0.01 かかる。
      ローカルに実行履歴を持って自前集計するほうが安いかもしれない）
