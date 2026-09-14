# アーキテクチャ

## 1. モジュール構成

```
src/shor_braket/
├── classical/
│   ├── preprocess.py      # 偶数判定・素数冪判定・gcd による早期終了
│   ├── postprocess.py     # 連分数展開による位数復元、因数の導出
│   └── order.py           # 参照実装（古典的な位数計算。テスト用の真値）
├── quantum/
│   ├── reference.py       # 密行列によるローカル専用の位数発見回路（実装済み）
│   └── (future)           # QPU 互換の可逆算術・QFT・デバイス別変換
├── runner/
│   ├── local.py           # LocalSimulator 実行
│   ├── braket.py          # AwsDevice 実行（SV1 / QPU）
│   └── gate.py            # 実行ゲート（validated レコードの発行・検証）
├── analysis/
│   ├── distribution.py    # 測定分布 vs 理想分布の比較
│   └── report.py          # 実行レポート生成
└── cli.py                 # コマンドラインインタフェース
```

**設計上の分離原則**: `quantum/` は Braket の `Circuit` オブジェクトを返すだけで、実行しない。
`runner/` が実行を担う。また、行列参照回路と QPU 互換回路を別実装にする。
参照回路をバックエンドだけ切り替えて QPU に送ることは禁止する。

---

## 2. 位数発見回路

### 2.0 ローカル参照回路と QPU 互換回路

Phase 1 では制御モジュラー乗算と逆 QFT を密なユニタリ行列として構成する。
N=15, a=7, t=8 では count 8 qubit + work 4 qubit の計 12 qubit となる。
これはアルゴリズム、レジスタ順序、後処理を検証するための正確な参照実装だが、密行列を
ハードウェアのネイティブゲートへそのまま投入できないため `local-reference` として扱う。

QPU 互換回路は Phase 2 後半で可逆算術ゲートから別に構築する。対象デバイスへ変換した後、
同じ回路を校正データ付き `LocalEmulator` で通したことを QPU 投入の前提にする。

### 2.1 全体構造

標準的な量子位相推定 (QPE)。

```
counting register (t qubits)   |0>^t ──[H]^t──●───●───●──[QFT†]──[measure]
                                             │   │   │
work register (n qubits)       |1>  ─────────[U^1][U^2][U^4]────────────
```

- 作業レジスタ: $n = \lceil \log_2 N \rceil$ qubit。初期状態 $|1\rangle$
- カウントレジスタ: $t$ qubit。測定値 $y$ から $y/2^t \approx s/r$ を連分数展開で復元
- $U_a: |x\rangle \mapsto |ax \bmod N\rangle$（$x < N$ の範囲。$x \geq N$ は恒等写像として拡張）

### 2.2 N = 6, a = 5 の場合の U

$5 \equiv -1 \pmod 6$ なので $U_5: |x\rangle \mapsto |(6-x) \bmod 6\rangle$。

| x (10進) | x (2進) | 5x mod 6 | 出力 (2進) |
|---|---|---|---|
| 0 | 000 | 0 | 000 |
| 1 | 001 | 5 | 101 |
| 2 | 010 | 4 | 100 |
| 3 | 011 | 3 | 011 |
| 4 | 100 | 2 | 010 |
| 5 | 101 | 1 | 001 |
| 6 | 110 | — | 110 (定義域外、恒等) |
| 7 | 111 | — | 111 (定義域外、恒等) |

置換として $(001\ 101)(010\ 100)$ の 2 つの互換の積。3 qubit 上の多重制御 X ゲートで構成できる。
$U_5^2 = I$（$r = 2$ の帰結）。

### 2.3 オラクルモード

Shor の実機デモは「答えを知った上で回路を最適化する」問題が繰り返し指摘されている
（Smolin et al. 2013）。本プロジェクトは 3 モードを明示的に区別し、**実行記録に必ずモードを残す**。

| モード | 制御-$U^{2^j}$ の構成法 | 位数 r の知識 | 妥当性 |
|---|---|---|---|
| `matrix-reference` | 定義通りの置換行列を生成 | 使わない | ◎ 正確なローカル基準。**QPU投入不可** |
| `generic-repeated` | 制御-$U_a$ を $2^j$ 回適用 | 使わない | ◎ 最も正直。t が小さいときのみ現実的。**N=15, t=2 で実装済み**（論理 2q ゲート 86） |
| `generic-constant` | $c = a^{2^j} \bmod N$ を古典計算し、制御-$U_c$ を構成 | 間接的に露出 | ○ Shor の標準的実装。多項式時間。**N=15 では swap network で実装済み**（`quantum/n15.py`、N=15 専用分解） |
| `compiled` | r を既知として手動最適化 | 使う | △ ハードウェア実現性の比較用。**Shor の実行とは記録しない** |

**`generic-constant` の注意点**: N = 6, a = 5 では $a^{2^1} = 25 \equiv 1 \pmod 6$ となり、
$j \geq 1$ の制御ゲートが恒等写像に退化する。これは「古典的に多項式時間で計算できる値」に
基づく正当な構成だが、結果として回路が $r = 2$ を露呈する。
N = 6 の実機実行では **`generic-repeated` を既定**とし、`generic-constant` との比較を記録する。

### 2.4 量子ビット・ゲート数の見積り

| ケース | n (work) | t (count) | 合計 qubit | 制御-U 適用回数 (repeated) |
|---|---|---|---|---|
| N=6, t=1 | 3 | 1 | 4 | 1 |
| N=6, t=2 | 3 | 2 | 5 | 3 |
| N=6, t=3 | 3 | 3 | 6 | 7 |
| N=15, t=4 | 4 | 4 | 8 | 15 |
| N=15, t=8 (教科書値 2n+1) | 4 | 8 | 12 | 255 |

**実測（2026-09-14、N=15, a=7, t=2, swap network）**: 論理 2 qubit ゲートは `generic-constant` 46、`generic-repeated` 86。
ルーティング後は IQM Garnet 88 / 179、IQM Emerald 91 / 161、AQT IBEX Q1 46 / 86（全結合で SWAP なし）。
格子型では SWAP が 2 qubit ゲートをほぼ 2 倍にする。Fredkin が三角形の接続を要求するのに格子は二部グラフだからで、
ルータの出来だけの問題ではない（Wiki「N=15 を QPU 互換回路で」）。

$r = 2$ のとき位相は $\{0, 1/2\}$ の 2 値であり **t = 1 で厳密に表現できる**。
N = 6 の実機実行は **t = 1 または t = 2** を既定とする（t = 1 のとき逆 QFT は Hadamard 1 枚）。

教科書値 $t = 2n+1$ は「任意の r に対して連分数展開が成功する」ための十分条件であり、
r が既知の小ささである本ケースでは過剰。ただし **t を小さく取ること自体が r の知識の利用**に
あたるため、実行記録には t の選択根拠を残す。

### 2.5 期待される測定分布

N = 15, a = 7, t = 8 では位数 r = 4 なので、カウントレジスタは
$y \in \{0, 64, 128, 192\}$、作業レジスタは軌道
$\{1, 7, 4, 13\}$ に限られる。両者の組み合わせ 16 状態が各 1/16 となり、
それ以外の状態の確率は 0 になる。

評価対象はカウントレジスタの周辺分布ではなく、**count + work の同時分布**とする。
カウント単独だと、N=6, a=5, t=1 の一様な理想分布を壊れた乱数出力と区別できないため
（[issue #6](https://github.com/s-sasaki-earthsea-wizard/shor-braket/issues/6)）。

N = 6, a = 5, t = 1 の場合、カウントレジスタの測定結果は

$$P(0) = P(1) = 0.5$$

t = 2 の場合、位相 $\{0, 1/2\}$ が $y \in \{0, 2\}$ に対応し

$$P(00) = P(10) = 0.5, \quad P(01) = P(11) = 0$$

実機ではノイズにより全ビット列に確率が漏れる。**同時理想分布との
Total Variation Distance / Hellinger fidelity を評価の主指標とする。**

### 2.6 反復的 QPE（Kitaev 型）という選択肢

IQM Garnet / Emerald はネイティブゲートに `cc_prx`（古典条件付き PRX）と
`measure_ff`（フィードフォワード測定）を持つ。すなわち**ミッドサーキット測定と
古典フィードフォワードが使える**（`docs/04-devices-and-cost.md` §4.2）。

これにより **反復的量子位相推定（semiclassical QFT）** が実装できる。

```
count qubit (1)  |0> ─[H]─●─[Rz(θ_k)]─[H]─[measure]─┐  … k = t-1, t-2, …, 0
                          │                          │
work register (n)   |1> ──[U^(2^k)]───────────────────┘  (θ_k は過去の測定結果から決まる)
```

| 方式 | qubit 数 (N=6) | 逆 QFT | 深さ |
|---|---|---|---|
| 通常 QPE (t=2) | 3 + 2 = 5 | 多重制御位相ゲートが必要 | 深い |
| 反復的 QPE | 3 + 1 = **4** | 不要（古典で位相補正） | **浅い** |

**t を増やしても qubit 数が増えない**点が本質的な利点。ノイズ耐性で有利。

ただし対応デバイスが限られる（Cepheus / IBEX / Forte Enterprise は非対応）ため、
**通常 QPE を基本実装とし、反復的 QPE を追加実装として比較する**方針とする。
どちらを使ったかは実行記録に残す。

**実装（2026-09-14、`quantum/n15_iterative.py`、`runner/n15_iterative.py`、`make emulate-n15-iterative`）**

- count qubit 1 個を `measure_ff` → `cc_prx(π, 0)` の能動リセットで再利用する。位相補正は
  `cc_prx(π, 0)` と `cc_prx(π, α/2)` の対（合成すると Rz(α)）で入れる。逆 QFT の制御位相が無くなり、
  N = 15 t = 2 の論理 2 qubit ゲートは 46 → 44（`generic-constant`）
- **実機は中間測定の結果を返さない**（開発者ガイド）。各ラウンドの結果を `cc_prx(π, 0)` で記録用 qubit に
  コピーして最終測定で読む。記録用 qubit はカプラ不要なので、ルータは核となる 5 qubit を配置した後に
  読み出し忠実度の高い空き qubit を割り当てる（`choose_layout(..., detached=...)`）
- 厳密分布は deferred measurement 変換（`measure_ff` → 補助 qubit への CNOT、`cc_prx` → 補助 qubit を
  制御とする 2 qubit ユニタリ）で `braket_dm` から解析的に求める。標本は feed-forward 回路そのものを
  shot ごとに走らせて取る
- SDK 1.127.0 のエミュレータのノイズモデルは `measure_ff` / `cc_prx` にノイズを付けない。同じ校正値から
  読み出し bit-flip（測定直前の状態反転）と 1 qubit depolarizing を手で足す
- 開発者ガイドの制約のうち、フィードバックキーの一意性・`cc_prx` が `measure_ff` の後に来ること・制御元が
  1 qubit であることは `feed_forward_violations` で検査する。**同一 qubit グループ内**の制約はグループが
  画像でしか公開されておらずオフラインでは検査できない

---

## 3. 実行フロー

```
cli.py
  │
  ├─ classical/preprocess ──→ 早期終了（N=6 なら本来ここで 2 が返る）
  │                            ※ 量子部分を走らせるため --force-quantum で迂回
  │
  ├─ quantum/order_finding ──→ Circuit（未実行）
  │
  ├─ runner/gate ──→ circuit_hash 計算
  │
  ├─ runner/local ──→ LocalSimulator 実行 → アサーション判定
  │                    pass なら runs/validated/<hash>.json を発行
  │
  └─ runner/braket ──→ gate 検証 → コスト確認 → AwsDevice.run()
```

**`--force-quantum` フラグ**: N = 6 は古典前処理のステップ 1（偶数判定）で即座に終了するため、
量子部分に到達しない。本プロジェクトの目的上、前処理を意図的に迂回するフラグを設ける。
このフラグが立った実行は、レポート上で「古典前処理を迂回した実行」と明記する。

---

## 4. 依存関係の方針

- **Braket SDK を第一級とする** — Qiskit からの変換レイヤは挟まない。デバイス固有のゲートセット・
  接続性の扱いが Braket ネイティブのほうが素直
- **古典参照実装を必ず持つ** — `classical/order.py` が真値を提供し、量子側の結果を検証する
- **回路の正規化は Braket IR で行う** — OpenQASM のテキスト比較は空白・命令順で揺れるため不適
