# CLAUDE.md — shor-braket

Shor のアルゴリズムの位数発見回路を実装し、ローカルシミュレータ検証を通ったものだけを
Amazon Braket で実行するプロジェクト。検証の主題は N = 15、当初要件の N = 6 は縮退ケースとして併設。
AWS リソースは Terraform 管理。

---

## 絶対に忘れてはいけない前提

### 0. 優先順位: まずローカル、AWS は後

**Phase 1–2（ローカルシミュレータ）が最優先。** AWS 側（Phase 3 以降）は設計だけ先に
固めてあり、着手はローカルが通ってから。AWS の作業を先回りして進めないこと。

### 1. サービス名は Amazon **Braket**（Bracket ではない）

コード・ドキュメント・パッケージ名はすべて `braket` で統一する。

### 2. N = 6 では Shor の量子部分が分解に寄与しない

$\mathbb{Z}_6^* = \{1, 5\}$ で使える底は a = 5 のみ。$5^2 \equiv 1 \pmod 6$ より r = 2 だが、
$a^{r/2} = 5 \equiv -1 \pmod 6$ となり Shor の成功条件を満たさない。
得られる因数 2 は「6 が偶数である」ことに等しい。詳細は `docs/01-why-n6-is-degenerate.md`。

**このプロジェクトで N = 6 の実行結果を「Shor による因数分解の成功」と記述してはならない。**
正しくは「a = 5 mod 6 の位数 r = 2 の量子位相推定による測定」。

**アルゴリズムの検証は N = 15（a = 7, r = 4 など）を主テストケースとして行う。** N = 6 は底が 1 つで
制御-U も実質 1 個しか働かず、QPE の配線ミスを検出できない。N = 6 は縮退ケースの回帰テストとして併設し、
「位数発見が分解に寄与したか」が常に false になることを確認する。

- **N = 15 でも「因数が出た」を成功条件にしない。** 連分数展開の保証窓は t に依存せず、一様乱数を返す
  デバイスでも 1 ショットあたり約 12% で 3 × 5 が得られる。評価指標は理想分布との TVD / Hellinger fidelity
- **実機ペイロードは Phase 2 でトランスパイル後の 2 qubit ゲート数を実測してから決める。** 設計段階の
  見積りでは N = 15 generic は数千ゲートで完走せず、N = 6 generic t = 1 が数十ゲートで下限ぎりぎり

### 3. 課金が発生する

Braket の QPU は「タスクあたり定額 + ショットあたり従量」。
**実機に投げるコードを書くときは必ず実行ゲート（`docs/03-execution-gate.md`）を経由させる。**
ゲートを迂回するコードパスを追加しない。テストで実デバイスを叩かない。

### 4. コンパイル済みオラクルを黙って使わない

位数 r を知った上で回路を最適化すると「答えを回路に埋め込む」ことになる
（Smolin et al. 2013 の批判）。オラクルモード（`generic-repeated` / `generic-constant` /
`compiled`）を必ず実行記録に残す。既定は `generic-repeated`。

---

## 開発ルール

- Python: PEP 8、関数 snake_case、クラス PascalCase、定数 UPPER_SNAKE_CASE、Docstring は Google Style
- コード内のコメント・ログ・エラーメッセージ・docstring は **英語**
- 応答・設計ドキュメントは **日本語**
- ブランチ: `feature/*`, `fix/*`, `refactor/*`。PR は main へ
- セッション記録は `.claude-notes/`（gitignore 対象）

### コミットメッセージ

英文・動詞始まり。絵文字プレフィクス:
✨ feat / 🐞 fix / 📚 docs / 🎨 style / 🛠️ refactor / ⚡ perf / ✅ test / 🏗️ chore /
🚀 deploy / 🔒 security / 📝 update / 🗑️ remove

Claude Code 経由のコミットには以下を含める:

```text
🤖 Assisted by [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

### ドキュメント同期

機能追加・Phase 完了時に以下を同期更新する:

1. `CLAUDE.md` — プロジェクト全体状況、Phase 完了記録、技術仕様
2. `README.md` — 機能概要、実装状況、使用方法、ステータス表
3. `Makefile` / `makefiles/*.mk` — `##` ヘルプテキスト

---

## 実装上の指針

- `quantum/` は `Circuit` を返すだけで実行しない。実行は `runner/` の責務
- 古典参照実装（`classical/order.py`）が常に真値を提供する。量子側の結果はこれと突き合わせる
- 回路ハッシュは OpenQASM のテキストではなく **正規化した Braket IR** に対して取る
- デバイス ARN をハードコードしない。論理名 → ARN のマップを設定ファイルに置き、
  起動時に `search-devices` で実在検証する
- Braket SDK を第一級とする。Qiskit からの変換レイヤは挟まない

---

## 秘密情報の扱い

**アカウント ID・バケット名・通知先メールアドレス等をリポジトリに書かない。**

| 用途 | 置き場所 | git |
|---|---|---|
| クライアント / Makefile | `.env`（テンプレート `.env.example`） | ignore |
| Terraform | `infra/terraform/terraform.tfvars`（テンプレート `*.tfvars.example`） | ignore |
| IAM ポリシーの実値展開 | `infra/iam/rendered/`（`make iam-render` が生成） | ignore |
| Terraform ステート | `infra/terraform/terraform.tfstate` | ignore |

`infra/iam/*.json` にはプレースホルダ（`<AWS_ACCOUNT_ID>` / `<RESULTS_BUCKET>`）のみ。
値を直書きしたコードやコマンド例をドキュメントに残さない。
コマンドライン引数で秘密値を渡す例も避ける（シェル履歴に残るため）。

---

## AWS

- **root アカウントのアクセスキーを使わない。**
- **プロファイルを権限で 2 つに分ける**（`infra/iam/README.md`）
  - `shor-braket-ro` — 読み取りのみ、MFA 不要
  - `shor-braket-exec` — タスク投入、**MFA 必須の assume role**
  - 読み取り系ターゲットは RO、`submit-*` は EXEC を使う
- **デバイス制限は `Allow` ではなく `Deny` で書く。** Braket の IAM リソースタイプは
  `quantum-task` のみで、デバイスは `Allow` の `Resource` で絞れない（AWS 公式仕様）
- MFA 判定は `Bool` ではなく **`BoolIfExists`** を使う。長期アクセスキーでは
  `aws:MultiFactorAuthPresent` キー自体が存在せず、`Bool` だと Deny が発動しない
- **ショット数を制限する IAM 条件キーは存在しない。** クライアント側 + AWS Budgets で守る
- Terraform で管理するもの: S3（結果保存）、IAM、AWS Budgets + SNS、CloudWatch ロググループ
- Terraform で管理しないもの: 量子タスク（使い捨ての実行単位であり状態管理対象として不適切）
- **Terraform ステートはローカル管理**（`backend.tf` を置かない）
- Braket の初回有効化（コンソールでの利用規約同意）は Terraform 不可。手動手順として記録する

---

## 決定事項

| 項目 | 決定 | 記録 |
|---|---|---|
| 対象 N | N = 15 を検証の主題、N = 6 を縮退ケースとして併設。実機ペイロードは Phase 2 の深さ実測で決定 | `docs/adr/0001-*.md` |
| ライセンス | Apache License 2.0 | `LICENSE` |
| Terraform ステート | ローカル管理 | `docs/adr/0002-*.md` |
| 月次予算 | **100 USD** | `docs/adr/0002-*.md` |
| 第一候補デバイス | IQM Garnet（feed-forward 対応 + 低単価） | `docs/04-devices-and-cost.md` |
| AWS プロファイル | 読み取り / 実行(MFA 必須) の 2 分割 | `docs/adr/0002-*.md` |

---

## Phase 進捗

| Phase | 内容 | 優先度 | 状態 |
|---|---|---|---|
| 0 | プロジェクト設計・ドキュメント | — | ✅ 2026-09-07 完了 |
| 1 | Shor 実装（古典前処理 + 位数発見回路） | **高** | ⬜ |
| 2 | ローカルシミュレータ検証と実行ゲート | **高** | ⬜ |
| 3 | Terraform による AWS リソース定義 | 低 | ⬜ |
| 4 | SV1 実行 | 低 | ⬜ |
| 5 | 実機 QPU 実行と結果分析 | 低 | ⬜ |
