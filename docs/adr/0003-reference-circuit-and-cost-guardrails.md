# ADR-0003: ローカル参照回路と QPU 互換回路を分け、QPU 費用をサービス側でも止める

- **状態**: 採用（2026-09-14）
- **日付**: 2026-09-14
- **決定者**: Syota Sasaki

> **一部置換済み (2026-09-15)**: 実行順から SV1 を外した。SV1 は verbatim 回路を実行できず、
> validated レコードを発行した回路そのものを投げられないため。
> [ADR-0004](0004-aqt-role-split-and-single-region.md) を参照。

## 背景

N=15 の正確な参照結果を得るには、モジュラー乗算を置換行列として実装するのが単純で検証しやすい。
一方、密行列は QPU のネイティブゲートへ現実的に分解できず、そのまま実機用回路にはできない。

また、クライアント側の shots・費用確認と AWS Budgets は操作ミスの防止と通知には使えるが、
QPU タスクの作成をサービス側で止めるハードストップではない。Amazon Braket Spending Limit が
デバイス単位で利用可能になったため、Terraform 管理へ組み込む必要がある。

## 決定

1. Phase 1 の回路を `local-reference` とし、`qpu_eligible = false` を結果へ記録する
2. QPU 互換回路は可逆算術ゲートを用いて別実装する
3. QPU 互換回路は対象デバイスの校正データを使う `LocalEmulator` を通してから投入する
4. QPU 候補を IQM Garnet、IQM Emerald、AQT IBEX-Q1 の 3 機に限定する
5. 3 機すべてに Braket Spending Limit を作り、初期値は 0 USD とする
6. 3 機へ配分する上限の合計が 300 USD を超える Terraform apply を失敗させる
7. Spending Limit の変更権限は Terraform 用 AdminRole にだけ残し、実行ロールは参照だけ許可する
8. 月次 100 USD の AWS Budget はシミュレータ、S3 などを含む通知用として併用する

## 実行順序

```
LocalSimulator（参照回路、無料）
  → QPU 互換回路を別実装
  → LocalSimulator（論理回路、無料）
  → LocalEmulator（対象機の制約・ノイズ、無料）
  → SV1（AWS 経路確認、課金あり）
  → QPU（Spending Limit の対象）
```

SV1 / DM1 は Braket Spending Limit の対象外である。実行にはシミュレータ用リージョンのS3とIAMが
必要であり、クライアント側の費用表示、明示確認、AWS Budgetで守る。

## 結果

ローカル参照回路の成功だけでは QPU 投入資格を発行しない。QPU 投入ゲートは、対象機へ変換した
回路のハッシュと LocalEmulator の合格記録が一致すること、Spending Limit が存在して有効期間内で
あること、残額が投入費用以上であることを検査する。
