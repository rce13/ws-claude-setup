# 日本株スクリーニング（優待込み）

財務スクリーニング → 優待突き合わせ → 税引後総合利回りで順位づけ、までの再現可能なパイプライン。

データ源とその制約は [`docs/jp-stock-data-sources.md`](../../docs/jp-stock-data-sources.md) を参照。

## 前提

```bash
export EDINETDB_API_KEY="..."   # https://edinetdb.jp/developers で無料発行
```

標準ライブラリのみで動くので追加インストールは不要。

## 流れ

```bash
PROJ=~/projects/$(date +%Y%m%d)_jpstock
mkdir -p $PROJ/results

# 1. 財務で数十社に絞る (EDINET DB を叩く。無料枠 100 req/日)
python 01_screen_fundamentals.py --dry-run          # まず消費見積り
python 01_screen_fundamentals.py --outdir $PROJ/results/01_screen

# 2. 通過した銘柄の優待を各社IRで確認して yutai_master.csv に記入
#    (ここは手作業。優待には公式APIが存在しない)

# 3. 株価を用意する (code,price の CSV)
#    J-Quants 無料プランは12週間遅延なので基準日を必ず記録すること

# 4. 税引後総合利回りで並べる
python 02_total_yield.py \
  --candidates $PROJ/results/01_screen/candidates_YYYYMMDD_HHMMSS.csv \
  --prices $PROJ/prices.csv \
  --outdir $PROJ/results/02_total_yield \
  --price-asof 2026-05-10
```

## 条件のカスタマイズ

`01_screen_fundamentals.py` の `DEFAULT_CRITERIA` を JSON で上書きできる。

```bash
cat > my_criteria.json <<'EOF'
{
  "pool_metrics": ["dividend_yield", "roe", "equity_ratio"],
  "pool_limit": 300,
  "pool_mode": "intersect",
  "max_verify": 60,
  "thresholds": {
    "roe":            [10.0, null],
    "equity_ratio":   [50.0, null],
    "pbr":            [null, 1.5],
    "dividend_yield": [2.5, null],
    "payout_ratio":   [null, 70.0]
  }
}
EOF
python 01_screen_fundamentals.py --config my_criteria.json --outdir $PROJ/results/01_screen
```

条件は `screen_log_{TS}.json` に丸ごと保存される。半年後に
「あのとき何を根拠に選んだのか」を再現できるのがこの形にしている理由。

## API 消費について

EDINET DB 無料枠は **100 req/日**。消費内訳は:

- `rankings` を指標ごとに 1 req（既定 3 req）
- 生き残り 1 社につき `ratios` 1 req（既定上限 60 req）

レスポンスは `cache/` に保存され、**再実行時は消費しない**。条件を変えて
試行錯誤しても、同じ銘柄を再取得する分にはクォータを食わない。
クォータ上限に当たった場合は途中まで書き出して終了し、翌日再実行すれば
キャッシュ済みを飛ばして続きから進む。

`cache/` は `.gitignore` 済み。消すと消費し直しになる。

## 優待の扱い

**株主優待には公式APIも無料の一括データセットも存在しない。**
主要サイトは規約でスクレイピングを禁止しているため、`yutai_master.csv` は
各社IRの一次情報から手で埋める設計にしてある。財務で数十社に絞った後なら
手入力は現実的だし、一次情報にあたる分だけ精度が高い。

同一銘柄で必要株数が複数ある場合（100株/300株/1000株）は**行を分ける**。
優待は 100 株で還元率が最大になり、単元を増やすと落ちるのが通例なので、
全単元を計算して `best_tier_{TS}.csv` に最適単元を出す。

### utility_factor

額面 6,000 円の食事券も、その店に行かないなら価値はほぼゼロ。
`utility_factor` (0.0–1.0) で「額面のうち自分にとって実際に価値がある割合」を指定する。

| 種類 | 目安 |
|---|---|
| QUOカード・金券 | 1.0 |
| カタログギフト | 0.7–0.9 |
| 自社製品 | 0.3–0.8（買うつもりがあったかで変わる） |
| 自社店舗の食事券・割引券 | 0.2–0.7（通える距離かで変わる） |

主観で構わない。むしろ**主観を明示的なパラメータとして外に出す**のがここの主旨で、
雑誌の「高利回りランキング」が実感と合わないのは、この係数を全部 1.0 と
置いているからだと考えている。

## 税引後で比較していること

配当は 20.315% 源泉徴収されるが、優待は現物給付で実務上ほぼ課税されない。
したがって同じ「利回り 3%」でも手取りが違う。

```
配当 3.0% → 手取り 2.39%
優待 3.0% → 手取り 3.00%
```

税引後で見ると優待は配当の約 1.25 倍の重みを持つ。表面利回りの単純合算は
この差を潰してしまうので、順位づけには税引後実効値を使い、表面値は併記のみにしている。

## 織り込んでいないもの

- **権利落ち** — 権利付最終日の翌日に優待+配当相当分だけ株価は下がる。
  優待狙いの直前買いは基本ゼロサム。長期保有前提の指標として読むこと。
- **優待廃止リスク** — 東証の株主平等原則の要請もあり廃止は増えている。
  財務の悪い会社から先に消えるので、01 の財務スクリーニングを通していること
  自体がリスク低減になっているが、ゼロにはならない。TDnet の適時開示で追う。
- **長期保有条件** — `long_hold_months` が立っている銘柄は初年度その利回りが出ない。
  出力の md に別枠で列挙される。

## 出力

| ファイル | 内容 |
|---|---|
| `candidates_{TS}.csv` | 財務スクリーニング通過銘柄 |
| `screen_log_{TS}.json` | 使用条件・API消費数・除外理由の内訳 |
| `total_yield_{TS}.csv` | 単元別の全パターン |
| `best_tier_{TS}.csv` | 各社の最適単元を税引後利回り順で |
| `total_yield_{TS}.md` | 読み物としての要約（乖離の大きい銘柄、長期条件つき銘柄を別掲） |
