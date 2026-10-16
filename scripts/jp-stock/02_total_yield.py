#!/usr/bin/env python3
"""
目的: 財務スクリーニング通過銘柄に株主優待を突き合わせ、税引後の総合利回りで並べ替える。

入力:
  - --candidates  01_screen_fundamentals.py が出した candidates_{TS}.csv
  - --yutai       yutai_master.csv (各社IRから手で埋めたもの)
  - --prices      code,price の CSV (株価。データ源は自分で選ぶ / 下の注意を読むこと)

出力:
  - {outdir}/total_yield_{TS}.csv       単元別の全パターン
  - {outdir}/best_tier_{TS}.csv         各社の最適単元だけを税引後利回り順で
  - {outdir}/total_yield_{TS}.md        読み物としての要約

計算の考え方 (ここが本体なので README ではなくコードのそばに書いておく):

1) 優待利回り = 優待額面 × 年間回数 ÷ (株価 × 必要株数)

2) 実効優待利回り = 優待利回り × utility_factor
   額面 6000 円の食事券も、その店に行かないなら価値はゼロに近い。
   額面をそのまま足す「表面総合利回り」は優待銘柄を系統的に過大評価する。

3) 税引後で比較する ★ここが効く
   配当は 20.315% 源泉徴収される。優待は現物給付で、実務上ほぼ課税されない。
   つまり「配当利回り 3.0%」と「優待利回り 3.0%」は手取りが違う。
     配当 3.0% → 手取り 2.39%
     優待 3.0% → 手取り 3.00%
   税引後で見ると優待は配当の約 1.25 倍の重みを持つ。単純合算はこれを潰してしまう。

4) 単元の非線形性
   優待は 100 株で還元率が最大になり、300 株・1000 株では逆に落ちるのが通例。
   同じ資金なら「1銘柄を1000株」より「10銘柄を100株ずつ」が優待的には有利。
   全単元を出力して best_tier で最適単元を明示する。

5) 織り込んでいないもの (承知の上で使うこと)
   - 権利落ち: 権利付最終日の翌日に優待+配当相当分だけ株価は下がる。
     優待狙いの直前買いは基本ゼロサム。長期保有を前提とした指標として読むこと。
   - 優待廃止リスク: 東証の株主平等原則の要請もあり廃止は増えている。
     財務が悪い会社の優待から先に消えるので、01 の財務スクリーニングを
     通していること自体がリスク低減になっている。それでもゼロにはならない。
   - 長期保有条件: long_hold_months が立っている銘柄は初年度その利回りが出ない。

株価データについて:
  J-Quants 無料プランは 12週間遅延。--prices にそれを入れると
  「3ヶ月前の株価による利回り」になる。現在値が要るなら別途調達すること。
  --price-asof に取得日を書いておくと出力に残る。

使い方:
  python 02_total_yield.py \
    --candidates results/01_screen/candidates_20260802_120000.csv \
    --yutai yutai_master.csv --prices prices.csv \
    --outdir results/02_total_yield
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

TAX_RATE = 0.20315  # 配当課税 (所得税15% + 復興特別2.1%相当 + 住民税5%)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        rows = []
        for row in csv.DictReader(f):
            # yutai_master.csv のコメント行 (# 始まり) を飛ばす
            code = (row.get("code") or "").strip()
            if not code or code.startswith("#"):
                continue
            rows.append({k: (v.strip() if isinstance(v, str) else v)
                         for k, v in row.items() if k})
        return rows


def num(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--yutai", default=str(Path(__file__).parent / "yutai_master.csv"))
    ap.add_argument("--prices", required=True, help="code,price の CSV")
    ap.add_argument("--outdir", default="results/02_total_yield")
    ap.add_argument("--price-asof", default="", help="株価の基準日 (出力に記録)")
    ap.add_argument("--min-utility", type=float, default=0.0,
                    help="utility_factor がこの値未満の優待は除外")
    args = ap.parse_args()

    cand = {r["code"]: r for r in read_csv(Path(args.candidates))}
    yutai = read_csv(Path(args.yutai))
    prices = {r["code"]: num(r.get("price")) for r in read_csv(Path(args.prices))}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    rows, skipped = [], []
    for y in yutai:
        code = y["code"]
        if code not in cand:
            skipped.append((code, y.get("name", ""), "財務スクリーニング未通過"))
            continue
        price = prices.get(code)
        if not price:
            skipped.append((code, y.get("name", ""), "株価データなし"))
            continue

        shares = num(y.get("tier_shares"), 100) or 100
        value = num(y.get("yutai_value_yen"), 0) or 0
        freq = num(y.get("times_per_year"), 1) or 1
        util = num(y.get("utility_factor"), 1.0)
        util = 1.0 if util is None else util
        if util < args.min_utility:
            skipped.append((code, y.get("name", ""), f"utility_factor={util} が閾値未満"))
            continue

        investment = price * shares
        if investment <= 0:
            skipped.append((code, y.get("name", ""), "投資金額が0"))
            continue

        gross_yutai = value * freq / investment * 100          # 額面ベース %
        eff_yutai = gross_yutai * util                          # 実効 %
        div = num(cand[code].get("dividend_yield"), 0.0) or 0.0
        div_after_tax = div * (1 - TAX_RATE)

        rows.append({
            "code": code,
            "name": y.get("name") or cand[code].get("name", ""),
            "tier_shares": int(shares),
            "price": round(price, 1),
            "investment_yen": int(investment),
            "dividend_yield_pct": round(div, 2),
            "dividend_after_tax_pct": round(div_after_tax, 2),
            "yutai_yield_gross_pct": round(gross_yutai, 2),
            "utility_factor": util,
            "yutai_yield_effective_pct": round(eff_yutai, 2),
            # 表面: よく雑誌で見る「総合利回り」。過大評価されがちなので併記だけ
            "total_yield_surface_pct": round(div + gross_yutai, 2),
            # 税引後実効: このパイプラインで実際に順位づけに使う指標
            "total_yield_after_tax_pct": round(div_after_tax + eff_yutai, 2),
            "yutai_type": y.get("yutai_type", ""),
            "record_months": y.get("record_months", ""),
            "long_hold_months": y.get("long_hold_months", "0"),
            "roe": cand[code].get("roe", ""),
            "equity_ratio": cand[code].get("equity_ratio", ""),
            "payout_ratio": cand[code].get("payout_ratio", ""),
            "source_url": y.get("source_url", ""),
        })

    if not rows:
        print("突き合わせ結果が0件。candidates と yutai_master の code が"
              "一致しているか確認してください。", file=sys.stderr)
        for c, n, why in skipped:
            print(f"  {c} {n}: {why}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: -r["total_yield_after_tax_pct"])
    cols = list(rows[0])

    all_path = outdir / f"total_yield_{ts}.csv"
    with all_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # 各社の最適単元だけ残す
    best: dict[str, dict] = {}
    for r in rows:
        cur = best.get(r["code"])
        if cur is None or r["total_yield_after_tax_pct"] > cur["total_yield_after_tax_pct"]:
            best[r["code"]] = r
    best_rows = sorted(best.values(), key=lambda r: -r["total_yield_after_tax_pct"])

    best_path = outdir / f"best_tier_{ts}.csv"
    with best_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(best_rows)

    # --- 読み物としての要約 ---
    md = [f"# 優待込み総合利回り {ts}", ""]
    if args.price_asof:
        md.append(f"株価基準日: **{args.price_asof}**")
    md += [
        f"対象 {len(best_rows)} 社 / 単元パターン {len(rows)} 件",
        "",
        "順位づけは **税引後実効総合利回り** による "
        f"(配当は {TAX_RATE*100:.3f}% 課税、優待は非課税として計算。"
        "優待額面には utility_factor を掛けている)。",
        "",
        "| 銘柄 | 単元 | 投資額 | 配当(税引後) | 優待(実効) | 税引後合計 | 表面合計 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in best_rows[:30]:
        md.append(
            f"| {r['code']} {r['name']} | {r['tier_shares']} | "
            f"{r['investment_yen']:,} | {r['dividend_after_tax_pct']}% | "
            f"{r['yutai_yield_effective_pct']}% | "
            f"**{r['total_yield_after_tax_pct']}%** | {r['total_yield_surface_pct']}% |"
        )

    gap = [r for r in best_rows
           if r["total_yield_surface_pct"] - r["total_yield_after_tax_pct"] > 1.0]
    if gap:
        md += ["", "## 表面利回りとの乖離が大きい銘柄", "",
               "utility_factor が低い (＝自分にとって使いにくい優待) か、"
               "配当比率が高く課税の影響を受けている銘柄。"
               "雑誌の「高利回りランキング」で上位に来るが実感が伴わないのはこの層。", ""]
        for r in gap[:10]:
            d = r["total_yield_surface_pct"] - r["total_yield_after_tax_pct"]
            md.append(f"- {r['code']} {r['name']}: 表面 {r['total_yield_surface_pct']}% "
                      f"→ 税引後実効 {r['total_yield_after_tax_pct']}% (差 {d:.2f}pt)")

    lh = [r for r in best_rows if num(r.get("long_hold_months"), 0)]
    if lh:
        md += ["", "## 長期保有条件あり (初年度はこの利回りが出ない)", ""]
        for r in lh:
            md.append(f"- {r['code']} {r['name']}: {r['long_hold_months']}ヶ月以上の継続保有が条件")

    if skipped:
        md += ["", "## 突き合わせ対象外", ""]
        for c, n, why in skipped:
            md.append(f"- {c} {n}: {why}")

    md += ["", "## 読むときの注意", "",
           "- 権利落ちを織り込んでいない。権利付最終日の直前に買っても翌日に"
           "優待+配当相当分だけ株価が下がるため、短期での取得は基本ゼロサム。",
           "- 優待廃止リスクは数値化していない。財務スクリーニングを通した銘柄に"
           "限っている分リスクは下がるが、ゼロではない。TDnet の適時開示で追うこと。",
           "- utility_factor は主観。同じ銘柄でも人によって順位は変わる。"
           "そこを明示的なパラメータにしてあるのがこのスクリプトの主旨。"]

    md_path = outdir / f"total_yield_{ts}.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"対象 {len(best_rows)} 社 / {len(rows)} 単元パターン")
    for p in (all_path, best_path, md_path):
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
