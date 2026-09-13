# Device snapshots

`snapshots/<key>.json` は Amazon Braket の `GetDevice` が返した **公開の校正データ** を、
ローカルエミュレータ（`LocalEmulator`）向けに保存したもの。QPU 3 機のみを扱う。

| key | デバイス | ARN |
|---|---|---|
| `garnet` | IQM Garnet | `arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet` |
| `emerald` | IQM Emerald | `arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald` |
| `ibex` | AQT IBEX Q1 | `arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1` |

## 中身

- `capabilities`: `deviceCapabilities` の JSON そのもの（qubit 数、ネイティブゲート、接続グラフ、
  1 qubit / 2 qubit の忠実度、shots 範囲、価格、実行窓）
- `capabilities_sha256`: 正規化した JSON のハッシュ。読み込み時に照合し、改変や破損を検出する
- `fetched_at` / `calibration_updated_at`: 取得時刻と、デバイス側の校正更新時刻
- キュー長など取得時点でしか意味を持たない値は保存しない

アカウント ID、バケット名、資格情報は含まれない。`make device-snapshot` は取得後に
`.env` のアカウント ID が混入していないことを確認する。

## 更新

```bash
make device-snapshot          # 読み取り専用プロファイルで GetDevice を 3 回呼ぶ。課金なし
make device-info DEVICE=garnet
```

校正データは日々変わる。エミュレーション結果は `result.json` に `capabilities_sha256` と
`calibration_updated_at` を記録するので、どの校正で得た結果かを後から追える。
古い校正での結果を現在値として扱わないこと。

## 使い方

```bash
make emulate DEVICE=garnet    # スナップショットからエミュレータを組み、オフラインで実行
make emulate-all              # 3 機比較
```

コンテナはネットワーク無効で動く。取得だけがホスト側の AWS CLI を使う。
