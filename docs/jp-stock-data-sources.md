# 日本株データ源と MCP セットアップ

ktomo-ws で日本株のスクリーニングを行うためのデータ源まとめ。
米国株の Financial Datasets MCP に相当するものは日本株には存在せず、複数を組み合わせる。

## データ源の比較

| 項目 | EDINET DB | J-Quants (JPX公式) | 優待データ |
|------|-----------|-------------------|-----------|
| 提供元 | edinetdb.jp (民間) | 日本取引所グループ | **公式APIなし** |
| データ | 有報ベースの財務・指標 | 株価四本値/出来高/財務/信用/空売り/先物オプション | — |
| カバレッジ | 全上場 3,848社 | 全上場 (無料は過去2年) | — |
| 料金 | 無料 (100 req/日) | 無料プランあり | — |
| **遅延** | 開示ベース (実質遅延なし) | **無料プランは12週間遅延** | — |
| MCP | 公式 `https://edinetdb.jp/mcp` | コミュニティ実装 | — |
| レート制限 | 100 req/日 (Free) | 5 req/分 (Free) | — |

### 遅延の意味

J-Quants 無料プランの株価は **12週間前まで**しか見えない。
「今日の終値でスクリーニングする」ことは無料枠では原理的にできない。

- **できる**: バックテスト、条件設計、財務スクリーニング、相対比較
- **できない**: 現在値ベースの利回り計算、エントリータイミング判断

現在値が要る場合は yfinance の `.T` ティッカー（非公式・自己責任）か証券会社API
（kabuステーション等、口座必須）を別途足す。本パイプラインは株価を手入力/CSV差し込み
できる設計にして、データ源を差し替え可能にしてある。

## セットアップ

### 1. EDINET DB (無料・登録のみ)

https://edinetdb.jp/developers でメール登録 → APIキー即発行。

```bash
export EDINETDB_API_KEY="..."   # ~/.bashrc に書く
```

### 2. J-Quants (無料プラン)

https://jpx-jquants.com/ で登録。V2 からはダッシュボード発行の APIキー方式
（V1 のリフレッシュトークン方式は廃止済み）。

```bash
uv pip install jquants-mcp
export JQUANTS_API_KEY="..."
```

### 3. MCP 登録

リポジトリ直下の `.mcp.json` がプロジェクトスコープで読まれる。
ユーザースコープに入れる場合は:

```bash
claude mcp add --transport http edinetdb https://edinetdb.jp/mcp \
  --header "Authorization: Bearer $EDINETDB_API_KEY"
claude mcp add jquants-mcp -- jquants-mcp
claude mcp list   # 接続確認
```

## REST API (スクリプトから叩く場合)

EDINET DB: ベース `https://edinetdb.jp/v1`、ヘッダ `X-API-Key: <KEY>`

| エンドポイント | 用途 |
|---|---|
| `GET /v1/companies` | 銘柄一覧 |
| `GET /v1/companies/{code}` | 企業詳細 |
| `GET /v1/companies/{code}/financials` | 財務時系列 (最大6年) |
| `GET /v1/companies/{code}/ratios` | 財務指標 |
| `GET /v1/companies/{code}/analysis` | 財務健全性スコア |
| `GET /v1/rankings/{metric}` | 指標別ランキング |
| `GET /v1/search?q=` | 企業検索 |

取得できる指標: `roe` `roa` `pbr` `per` `equity_ratio` `current_ratio` `de_ratio`
`dividend_yield` `payout_ratio` `doe` `operating_margin` `net_margin`
`revenue_cagr_3y` `oi_cagr_3y` `eps_growth` `fcf_yield` `ev_ebitda` ほか。

### 100 req/日 制約への対処

全銘柄ループは不可能。以下の順序で絞る:

1. `rankings` を指標ごとに叩く（3〜5 req）→ ローカルで積集合を取る
2. 残った数十社にだけ `ratios` / `analysis` を叩く（〜50 req）
3. レスポンスは必ずディスクにキャッシュする（`01_screen_fundamentals.py` は実装済み）

## 株主優待データについて

**公式APIも無料の一括データセットも存在しない。**

- JPX / EDINET / J-Quants のいずれにも優待情報は含まれない
- 株探などの主要サイトは利用規約でスクレイピングを禁止している
- 証券会社の優待検索はログイン前提で、規約上スクレイプ不可が通常

現実的な運用は **一次情報を手で埋める**:

1. 財務スクリーニングで候補を数十社に絞る（ここまでは自動）
2. 各社IRの株主優待ページで内容・必要株数・権利確定月を確認
3. `yutai_master.csv` に記入
4. `02_total_yield.py` で総合利回りを計算

優待の新設・変更・廃止は TDnet 適時開示に出るので、継続監視するならそちらを見る。
数十社なら手入力は現実的だし、一次情報にあたる分だけ精度は高い。

## 参考

- J-Quants API — https://www.jpx.co.jp/markets/other-data-services/j-quants-api/
- EDINET DB Developers — https://edinetdb.jp/developers
- jquants-mcp — https://github.com/shigechika/jquants-mcp
- jquants-free-mcp-server — https://github.com/cygkichi/jquants-free-mcp-server
