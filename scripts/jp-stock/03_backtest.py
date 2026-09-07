#!/usr/bin/env python3
"""
目的: スクリーニング通過銘柄が、過去に半年でどれだけ動いたかを J-Quants で検証する。
      「半年で+10%」が現実的な目標なのかを、願望ではなく分布として見る。

入力:
  - 環境変数 JQUANTS_API_KEY
  - --candidates  01_screen_fundamentals.py が出した candidates_{TS}.csv

出力:
  - {outdir}/backtest_returns_{TS}.csv   銘柄×エントリー日ごとの半年後リターン
  - {outdir}/backtest_summary_{TS}.md    分布・勝率・+10%達成率・TOPIX比較
  - cache/jquants/                        日次バーのキャッシュ

★ 無料プランの12週間遅延はここでは一切問題にならない。過去の話だからである。
  J-Quants 無料プラン（過去2年・12週間遅延）は、まさにこの用途のための制約。

★★ この検証のバイアスを理解した上で読むこと（重要）

  1) ルックアヘッド・バイアス
     「今の財務諸表」で選んだ銘柄に「過去の株価」を当てている。
     2年前の時点でこの銘柄群を選べたわけではない。
     → 読み方: 「この条件で選んだ銘柄群が過去どう動いたか」であって
       「この戦略で過去に儲かったか」ではない。後者を測るには
       各時点で入手可能だった財務データで選び直す必要がある（無料枠では困難）。

  2) 生存者バイアス
     現在上場している銘柄だけを見ている。上場廃止・経営破綻した銘柄は
     candidates に入らないので、リターンは実態より上に出る。

  3) サンプル数
     過去2年でエントリー日を月次にとっても、半年保有なら重複しない期間は
     実質3〜4区間しかない。信頼区間は広い。中央値の±数ポイントは誤差。

  この3つがある以上、出てくる数字は「期待値の粗い当たり」であって
  予測ではない。TOPIX との比較を必ず併記するのはそのため。

API 制限:
  無料プラン 5 req/分。60銘柄なら約12分かかる。キャッシュ済みは消費しない。

使い方:
  python 03_backtest.py --candidates results/01_screen/candidates_*.csv \
    --outdir results/03_backtest --hold-days 126 --target 10.0
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.jquants.com/v2"
CACHE_DIR = Path(__file__).parent / "cache" / "jquants"
FREE_PLAN_RPM = 5


class RateLimiter:
    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self.hits: deque[float] = deque()

    def acquire(self) -> None:
        now = time.monotonic()
        while self.hits and now - self.hits[0] > 60:
            self.hits.popleft()
        if len(self.hits) >= self.rpm:
            wait = 60 - (now - self.hits[0]) + 0.5
            print(f"  レート制限待機 {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            return self.acquire()
        self.hits.append(time.monotonic())


def api_get_all(path: str, params: dict, api_key: str, rl: RateLimiter) -> list[dict]:
    """ページネーションを辿って全件取得。結果はキャッシュ。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = path.strip("/").replace("/", "_")
    tag = "_".join(f"{k}{v}" for k, v in sorted(params.items()) if v)
    cache_file = CACHE_DIR / f"{slug}_{tag}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    rows: list[dict] = []
    p = dict(params)
    for _ in range(50):  # ページ上限
        rl.acquire()
        url = f"{BASE_URL}{path}?{urlencode({k: v for k, v in p.items() if v})}"
        req = Request(url, headers={"x-api-key": api_key, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            if e.code == 429:
                time.sleep(20)
                continue
            if e.code in (403, 404):  # プラン制限 / 該当なし
                print(f"  {path} {params}: HTTP {e.code} {body}", file=sys.stderr)
                return []
            raise RuntimeError(f"HTTP {e.code} {path}: {body}") from e
        except URLError as e:
            raise RuntimeError(f"接続失敗 {path}: {e}") from e

        rows.extend(payload.get("data", []))
        key = payload.get("pagination_key")
        if not key:
            break
        p["pagination_key"] = key

    cache_file.write_text(json.dumps(rows, ensure_ascii=False))
    return rows


def close_series(rows: list[dict]) -> list[tuple[str, float]]:
    """(日付, 調整後終値) の昇順リスト。AdjC 優先、無ければ C。"""
    out = []
    for r in rows:
        d = str(r.get("Date") or "")
        v = r.get("AdjC")
        if v in (None, ""):
            v = r.get("C")
        try:
            c = float(v)
        except (TypeError, ValueError):
            continue
        if d and c > 0:
            out.append((d, c))
    return sorted(out)


def forward_returns(series: list[tuple[str, float]], hold: int,
                    step: int) -> list[tuple[str, float]]:
    """step 営業日ごとにエントリーし、hold 営業日後のリターン(%)を返す。"""
    out = []
    for i in range(0, len(series) - hold, step):
        d0, p0 = series[i]
        _, p1 = series[i + hold]
        out.append((d0, (p1 / p0 - 1) * 100))
    return out


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--outdir", default="results/03_backtest")
    ap.add_argument("--hold-days", type=int, default=126,
                    help="保有営業日数 (126 ≈ 半年)")
    ap.add_argument("--step-days", type=int, default=21,
                    help="エントリー間隔の営業日数 (21 ≈ 月次)")
    ap.add_argument("--target", type=float, default=10.0,
                    help="達成率を測る目標リターン (%%)")
    ap.add_argument("--years", type=float, default=2.0, help="遡る年数")
    ap.add_argument("--max-codes", type=int, default=60,
                    help="API消費を抑える上限。5req/分なので60で約12分")
    args = ap.parse_args()

    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        print("JQUANTS_API_KEY が未設定。https://jpx-jquants.com/ で取得",
              file=sys.stderr)
        return 1

    with open(args.candidates, encoding="utf-8-sig") as f:
        cands = [r for r in csv.DictReader(f) if (r.get("code") or "").strip()]
    if not cands:
        print("candidates が空", file=sys.stderr)
        return 1
    cands = cands[: args.max_codes]

    today = datetime.now()
    date_to = (today - timedelta(days=90)).strftime("%Y%m%d")   # 12週遅延を見込む
    date_from = (today - timedelta(days=int(365 * args.years))).strftime("%Y%m%d")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    rl = RateLimiter(FREE_PLAN_RPM)

    print(f"{len(cands)} 銘柄 / {date_from}-{date_to} / 保有{args.hold_days}営業日")

    # --- ベンチマーク (TOPIX) ---
    bench: list[tuple[str, float]] = []
    try:
        bench = close_series(api_get_all("/indices/bars/daily/topix",
                                         {"from": date_from, "to": date_to},
                                         api_key, rl))
    except RuntimeError as e:
        print(f"  TOPIX 取得失敗（比較なしで続行）: {e}", file=sys.stderr)
    bench_rets = [r for _, r in forward_returns(bench, args.hold_days, args.step_days)]

    # --- 個別銘柄 ---
    records, all_rets, per_stock = [], [], []
    for n, c in enumerate(cands, 1):
        code = c["code"].strip()
        name = c.get("name", "")
        print(f"[{n}/{len(cands)}] {code} {name}")
        rows = api_get_all("/equities/bars/daily",
                           {"code": code, "from": date_from, "to": date_to},
                           api_key, rl)
        s = close_series(rows)
        if len(s) < args.hold_days + 2:
            print(f"  データ不足 ({len(s)}営業日) — スキップ", file=sys.stderr)
            continue
        rets = forward_returns(s, args.hold_days, args.step_days)
        for d, r in rets:
            records.append({"code": code, "name": name, "entry_date": d,
                            "return_pct": round(r, 2)})
            all_rets.append(r)
        vals = [r for _, r in rets]
        per_stock.append({
            "code": code, "name": name, "n": len(vals),
            "median_pct": round(statistics.median(vals), 2),
            "hit_target_pct": round(100 * sum(v >= args.target for v in vals) / len(vals), 1),
            "worst_pct": round(min(vals), 2),
            "best_pct": round(max(vals), 2),
            "dividend_yield": c.get("dividend_yield", ""),
        })

    if not all_rets:
        print("リターンを計算できた銘柄がありません。", file=sys.stderr)
        return 1

    with (outdir / f"backtest_returns_{ts}.csv").open(
            "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)

    per_stock.sort(key=lambda r: -r["median_pct"])
    with (outdir / f"backtest_by_stock_{ts}.csv").open(
            "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(per_stock[0]))
        w.writeheader()
        w.writerows(per_stock)

    hit = 100 * sum(r >= args.target for r in all_rets) / len(all_rets)
    win = 100 * sum(r > 0 for r in all_rets) / len(all_rets)

    md = [
        f"# 半年リターン検証 {ts}", "",
        f"対象 {len(per_stock)} 銘柄 / 観測 {len(all_rets)} 件 "
        f"(保有 {args.hold_days} 営業日、{args.step_days} 営業日ごとにエントリー)",
        f"期間 {date_from}–{date_to}", "",
        "## 分布", "",
        "| 指標 | 銘柄群 | TOPIX |", "|---|---:|---:|",
    ]

    def row(label, f_):
        b = f"{f_(bench_rets):.1f}%" if bench_rets else "—"
        md.append(f"| {label} | {f_(all_rets):.1f}% | {b} |")

    row("中央値", lambda x: statistics.median(x))
    row("平均", lambda x: statistics.fmean(x))
    row("下位10%", lambda x: pct(x, 0.10))
    row("上位10%", lambda x: pct(x, 0.90))
    row("最悪", min)
    row("最良", max)

    md += ["", "## 目標達成率", "",
           f"- **+{args.target:g}% 以上**: {hit:.1f}% の確率で達成",
           f"- プラス圏で終了: {win:.1f}%"]
    if bench_rets:
        bh = 100 * sum(r >= args.target for r in bench_rets) / len(bench_rets)
        md.append(f"- 同期間の TOPIX が +{args.target:g}% 以上だった割合: {bh:.1f}%")
        md += ["", f"銘柄群の中央値 {statistics.median(all_rets):.1f}% に対し "
                   f"TOPIX は {statistics.median(bench_rets):.1f}%。"
                   "差が小さければ、選別ではなく相場全体を買っているのと変わらない。"]

    md += ["", "## 銘柄別 中央値 上位15", "",
           "| 銘柄 | 中央値 | +目標達成率 | 最悪 | 最良 | 配当利回り |",
           "|---|---:|---:|---:|---:|---:|"]
    for r in per_stock[:15]:
        md.append(f"| {r['code']} {r['name']} | {r['median_pct']}% | "
                  f"{r['hit_target_pct']}% | {r['worst_pct']}% | {r['best_pct']}% | "
                  f"{r['dividend_yield']} |")

    md += ["", "## この数字の読み方", "",
           "- **ルックアヘッド**: 今の財務で選んだ銘柄に過去の株価を当てている。"
           "2年前にこの銘柄群を選べたわけではない。戦略の検証ではなく、"
           "「この条件で選ばれる銘柄群の性質」を見ているだけ。",
           "- **生存者バイアス**: 現在上場している銘柄しか見ていない。"
           "消えた銘柄が入らない分、数字は実態より良く出る。",
           f"- **サンプル数**: 半年保有なら重複しない期間は実質3〜4区間。"
           f"観測 {len(all_rets)} 件と言っても独立ではない。中央値の±数ptは誤差。",
           "- **配当・優待を含まない**: 株価のリターンのみ。"
           "インカムは 02_total_yield.py の側で見ること。",
           "", "TOPIX との差が小さいなら、それは銘柄選別が効いていないという"
           "陰性の結果であって、条件を作り直す根拠になる。"]

    (outdir / f"backtest_summary_{ts}.md").write_text("\n".join(md) + "\n",
                                                      encoding="utf-8")
    print(f"\n中央値 {statistics.median(all_rets):.1f}% / "
          f"+{args.target:g}%達成率 {hit:.1f}% / 勝率 {win:.1f}%")
    print(f"  {outdir}/backtest_summary_{ts}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
