#!/usr/bin/env python3
"""
目的: 仮想ポートフォリオの損益を J-Quants の最新データで更新し、日次レポートを書く。

入力:
  - 環境変数 JQUANTS_API_KEY
  - portfolio/positions.csv   建玉 (code,name,shares,entry_price,entry_date)

出力:
  - portfolio/history.csv              日次の評価額を追記（1日1行）
  - portfolio/reports/{日付}.md        その日のレポート
  - 標準出力に要約

★ J-Quants 無料プランのデータ提供範囲は「直近から約12週間前まで」で、
  この窓は毎日1営業日ずつ前進する。つまり本スクリプトを毎日走らせると
  「3ヶ月前の相場が1日ずつ開いていく」形で追跡できる。
  リアルタイムではないが、値動きは実物であり、結果は事前には分からない。

  提供範囲はエラーメッセージで返るので、窓の外を要求した場合は
  その旨を表示して終了する（消費した扱いにはならない）。

使い方:
  python 04_track_portfolio.py
  python 04_track_portfolio.py --asof 2026-05-15   # 特定日で評価
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.jquants.com/v2"
ROOT = Path(__file__).parent
PF_DIR = ROOT / "portfolio"
CACHE = ROOT / "cache" / "jquants"
FREE_RPM = 5
TOPIX_KEY = "__TOPIX__"


class RateLimiter:
    def __init__(self, rpm: int) -> None:
        self.rpm, self.hits = rpm, deque()

    def acquire(self) -> None:
        now = time.monotonic()
        while self.hits and now - self.hits[0] > 60:
            self.hits.popleft()
        if len(self.hits) >= self.rpm:
            wait = 60 - (now - self.hits[0]) + 1
            print(f"  レート制限待機 {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            return self.acquire()
        self.hits.append(time.monotonic())


COVERAGE_RE = re.compile(r"covers the following dates:\s*([\d-]+)\s*~\s*([\d-]+)")


def coverage_end(message: str) -> str | None:
    """『Your subscription covers the following dates: A ~ B』から B を取り出す。"""
    m = COVERAGE_RE.search(message or "")
    return m.group(2).replace("-", "") if m else None


def fetch(path: str, params: dict, key: str, rl: RateLimiter,
          _retry: bool = True) -> tuple[list[dict], str]:
    """(rows, note) を返す。

    提供範囲外を要求すると J-Quants はレンジごと拒否してメッセージだけ返すので、
    そのメッセージから範囲の終端を読み取って一度だけ再試行する。
    無料プランの窓は毎日1営業日ずつ前進するため、終端を決め打ちにはできない。
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = "_".join(f"{k}{v}" for k, v in sorted(params.items()) if v)
    cf = CACHE / f"{path.strip('/').replace('/','_')}_{tag}.json"
    if cf.exists():
        return json.loads(cf.read_text()), ""

    rows, note, p = [], "", dict(params)
    for _ in range(30):
        rl.acquire()
        url = f"{BASE_URL}{path}?{urlencode({k: v for k, v in p.items() if v})}"
        req = Request(url, headers={"x-api-key": key, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode())
        except HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code == 429:
                time.sleep(20)
                continue
            # 提供範囲外は HTTP 400 + メッセージで返る。終端を読み取って一度だけ再試行。
            end = coverage_end(body)
            if end and _retry and params.get("to", "") > end:
                return fetch(path, {**params, "to": end}, key, rl, _retry=False)
            return [], f"HTTP {e.code}: {body}"
        except URLError as e:
            return [], f"接続失敗: {e}"

        if "message" in payload and not payload.get("data"):
            msg = payload["message"]
            end = coverage_end(msg)
            if end and _retry and params.get("to", "") > end:
                clamped = {**params, "to": end}
                return fetch(path, clamped, key, rl, _retry=False)
            return [], msg
        rows.extend(payload.get("data", []))
        nxt = payload.get("pagination_key")
        if not nxt:
            break
        p["pagination_key"] = nxt

    if rows:
        cf.write_text(json.dumps(rows, ensure_ascii=False))
    return rows, note


def _close_of(r: dict) -> float | None:
    v = r.get("AdjC")
    if v in (None, ""):
        v = r.get("C")
    try:
        c = float(v)
    except (TypeError, ValueError):
        return None
    return c if c > 0 else None


def last_close(rows: list[dict], asof: str | None) -> tuple[str, float] | None:
    """最終営業日の終値。AdjC 優先。asof 指定時はその日以前で最後のもの。"""
    best = None
    for r in rows:
        d = str(r.get("Date") or "")
        if asof and d > asof:
            continue
        v = r.get("AdjC")
        if v in (None, ""):
            v = r.get("C")
        try:
            c = float(v)
        except (TypeError, ValueError):
            continue
        if c > 0 and (best is None or d > best[0]):
            best = (d, c)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", help="評価基準日 YYYY-MM-DD (省略時は取得できる最新)")
    ap.add_argument("--positions", default=str(PF_DIR / "positions.csv"))
    args = ap.parse_args()

    key = os.environ.get("JQUANTS_API_KEY")
    if not key:
        print("JQUANTS_API_KEY が未設定", file=sys.stderr)
        return 1

    pos_path = Path(args.positions)
    if not pos_path.exists():
        print(f"{pos_path} がありません", file=sys.stderr)
        return 1
    with pos_path.open(encoding="utf-8-sig") as f:
        positions = [r for r in csv.DictReader(f)
                     if (r.get("code") or "").strip() and not r["code"].startswith("#")]

    rl = RateLimiter(FREE_RPM)
    entry_date = min(p["entry_date"] for p in positions)
    frm = (datetime.strptime(entry_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y%m%d")
    to = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

    rows_out, notes = [], []
    total_cost = total_value = 0.0
    asof_seen = []

    for p in positions:
        code5 = p["code"].strip()
        code5 = code5 if len(code5) == 5 else code5 + "0"
        rows, note = fetch("/equities/bars/daily",
                           {"code": code5, "from": frm, "to": to}, key, rl)
        if note:
            notes.append(f"{p['code']} {p['name']}: {note}")
        lc = last_close(rows, args.asof)
        shares = float(p["shares"])
        entry = float(p["entry_price"])
        cost = shares * entry
        total_cost += cost
        if not lc:
            rows_out.append({**p, "asof": "-", "price": "", "value": "",
                             "pl_yen": "", "pl_pct": "", "note": "価格取得できず"})
            total_value += cost
            continue
        d, price = lc
        asof_seen.append(d)
        value = shares * price
        total_value += value
        rows_out.append({
            "code": p["code"], "name": p["name"], "shares": int(shares),
            "entry_price": entry, "asof": d, "price": price,
            "value": round(value), "pl_yen": round(value - cost),
            "pl_pct": round((price / entry - 1) * 100, 2), "note": "",
        })

    asof = args.asof or (max(asof_seen) if asof_seen else "-")

    # --- 市場ベンチマーク ---
    # TOPIX 指数エンドポイントは無料プランでは 403。代わりに全上場銘柄の
    # 日次バー（1リクエストで約4,450銘柄）を2日ぶん取り、両日に存在する銘柄の
    # 騰落率の中央値を「等加重の市場リターン」として使う。
    # 中央値にしているのは、値がさ株や少数の急騰銘柄に引っ張られないため。
    bench_line = ""
    if asof != "-" and asof != entry_date:
        snaps = {}
        for d in (entry_date, asof):
            rows, note = fetch("/equities/bars/daily",
                               {"date": d.replace("-", "")}, key, rl)
            if note:
                notes.append(f"市場ベンチマーク {d}: {note}")
            snaps[d] = {r["Code"]: c for r in rows
                        if (c := _close_of(r)) is not None}
        a, b = snaps.get(entry_date, {}), snaps.get(asof, {})
        common = [(b[k] / a[k] - 1) * 100 for k in a.keys() & b.keys() if a[k] > 0]
        if common:
            common.sort()
            med = common[len(common) // 2]
            up = 100 * sum(1 for x in common if x > 0) / len(common)
            bench_line = (f"市場全体（{len(common):,}銘柄の等加重中央値）**{med:+.2f}%**"
                          f"　上昇銘柄比率 {up:.0f}%")
    pl = total_value - total_cost
    pl_pct = (total_value / total_cost - 1) * 100 if total_cost else 0.0

    PF_DIR.mkdir(exist_ok=True)
    (PF_DIR / "reports").mkdir(exist_ok=True)

    # --- history.csv に追記（同じ asof は上書き） ---
    hist_path = PF_DIR / "history.csv"
    hist = []
    if hist_path.exists():
        with hist_path.open(encoding="utf-8-sig") as f:
            hist = [r for r in csv.DictReader(f) if r.get("asof") != asof]
    hist.append({"asof": asof, "total_cost": round(total_cost),
                 "total_value": round(total_value), "pl_yen": round(pl),
                 "pl_pct": round(pl_pct, 2),
                 "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    hist.sort(key=lambda r: r["asof"])
    with hist_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(hist[0]))
        w.writeheader()
        w.writerows(hist)

    # --- レポート ---
    md = [f"# 仮想ポートフォリオ {asof} 時点", "",
          f"建玉日 {entry_date} / 元本 ¥{total_cost:,.0f}", "",
          f"## 評価額 ¥{total_value:,.0f}　損益 **{pl:+,.0f}円 ({pl_pct:+.2f}%)**", ""]
    if bench_line:
        md += [bench_line, ""]
    md += ["| 銘柄 | 株数 | 建値 | 現値 | 評価額 | 損益 | 騰落率 |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows_out, key=lambda r: -(r["pl_pct"] if isinstance(r.get("pl_pct"), float) else -99)):
        if r.get("note"):
            md.append(f"| {r['code']} {r['name']} | {r['shares']} | {r['entry_price']} "
                      f"| — | — | — | {r['note']} |")
        else:
            md.append(f"| {r['code']} {r['name']} | {r['shares']} | {r['entry_price']:,.0f} "
                      f"| {r['price']:,.0f} | {r['value']:,.0f} | {r['pl_yen']:+,.0f} "
                      f"| {r['pl_pct']:+.2f}% |")

    if len(hist) > 1:
        md += ["", "## 推移", "", "| 日付 | 評価額 | 損益率 |", "|---|---:|---:|"]
        for h in hist[-15:]:
            md.append(f"| {h['asof']} | ¥{int(h['total_value']):,} | {float(h['pl_pct']):+.2f}% |")

    if notes:
        md += ["", "## 注記", ""] + [f"- {n}" for n in notes]

    md += ["", "---", "",
           "仮想売買。J-Quants 無料プランの提供範囲（直近から約12週間前まで）で評価しているため、",
           "現在の市場価格ではない。窓は毎日1営業日ずつ前進する。"]

    rp = PF_DIR / "reports" / f"{asof}.md"
    rp.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{asof} 評価額 ¥{total_value:,.0f} 損益 {pl:+,.0f}円 ({pl_pct:+.2f}%)")
    if bench_line:
        print("  " + bench_line.replace("**", ""))
    for r in rows_out:
        if r.get("note"):
            print(f"  {r['code']} {r['name']}: {r['note']}")
        else:
            print(f"  {r['code']} {r['name']:14s} {r['pl_pct']:+6.2f}% ({r['pl_yen']:+,.0f}円)")
    print(f"\n  {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
