#!/usr/bin/env python3
"""
目的: EDINET DB のランキング API を組み合わせて日本株を絞り込む。

入力:
  - 環境変数 EDINETDB_API_KEY
  - --config で指定する JSON (criteria/ にプリセットあり)

出力:
  - {outdir}/candidates_{TS}.csv   通過銘柄と各指標の値
  - {outdir}/screen_log_{TS}.json  条件・API消費数・各ランキングの値域・注意事項

★ API 仕様（2026-08 に実データで確認済み）

  GET /v1/rankings/{metric}?limit=N
    - metric は **ハイフン区切り** (equity-ratio であって equity_ratio ではない)
    - **limit は最大 500**。それ以上を指定しても 500 件で頭打ち
    - **指標ごとに「良い順」でソート済み**。ただし何が「良い」かは API 側の定義:
        pbr              → 低い順  (0.195 → 0.595)
        roe              → 高い順  (92.2% → 18.5%)
        shares-change-5y → 減少幅が大きい順 (-99.8% → -6.2%)  ＝自社株買い検出に使える
        payout-ratio     → **高い順** (200% → 60.6%)
        ここが罠で、payout-ratio は「配当性向が高い順」なので
        「増配余地のある低配当性向の会社」を探す用途には**使えない**。
    - 返る値は人間スケール (% は %、PBR は倍)
    - 1リクエストで最大500銘柄ぶんの値が取れるので、銘柄ごとの個別呼び出しは不要

  GET /v1/companies/{code}/ratios
    - {code} は **edinet_code (E03006)**。証券コードでは not_found になる
    - **時系列が古い順**で返る。最新期を見るには末尾/最大 fiscal_year を取ること
    - 値は **小数** (roe 0.1158 = 11.58%)。ランキング側は % なので**単位系が違う**

★ 全銘柄の網羅はできない
  各ランキングは上位500件（全上場約3,800社の約13%）しか見えない。
  よって「全条件を満たす銘柄」ではなく「各指標の上位500に同時に入る銘柄」を
  探している。条件を増やすほど積集合は急速に小さくなる（実測: 3本で5社）。
  そのため必須条件 (require) は2〜3本に絞り、残りは加点 (score) で扱う設計にした。

使い方:
  python 01_screen_fundamentals.py --config criteria/value_rerating.json \\
    --outdir ~/projects/20260803_jpstock/results/01_screen
  python 01_screen_fundamentals.py --config criteria/income.json --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://edinetdb.jp/v1"
CACHE_DIR = Path(__file__).parent / "cache"
DAILY_QUOTA = 100          # Free プラン
RANKING_MAX_LIMIT = 500    # API 側の上限

# ratios エンドポイントは小数、ランキングは % で返す。揃えるための係数。
RATIO_PCT_FIELDS = {
    "roe", "roa", "roic", "equity_ratio", "net_margin", "operating_margin",
    "dividend_yield", "payout_ratio", "doe", "fcf_yield", "earnings_yield",
    "effective_tax_rate",
}


class Quota:
    def __init__(self, limit: int) -> None:
        self.limit, self.used = limit, 0

    def spend(self) -> None:
        if self.used + 1 > self.limit:
            raise RuntimeError(
                f"API 消費上限 {self.limit} に到達。キャッシュを残したまま翌日再実行してください。")
        self.used += 1


def api_get(path: str, params: dict, api_key: str, quota: Quota) -> dict:
    """GET + ディスクキャッシュ。キャッシュヒット時はクォータを消費しない。"""
    qs = urlencode(sorted(params.items()))
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{path.strip('/').replace('/', '_')}_{qs or 'none'}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    quota.spend()
    url = f"{BASE_URL}{path}?{qs}" if qs else f"{BASE_URL}{path}"
    req = Request(url, headers={"X-API-Key": api_key, "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            cache_file.write_text(json.dumps(data, ensure_ascii=False))
            return data
        except HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            if e.code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            return {"error": {"code": f"http_{e.code}", "message": body}}
        except URLError as e:
            if attempt == 3:
                raise RuntimeError(f"接続失敗 {path}: {e}") from e
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"{path}: 再試行上限")


def metric_path(metric: str) -> str:
    """equity_ratio / equity-ratio どちらで書かれても API 形式に正規化する。"""
    return metric.replace("_", "-")


def fetch_ranking(metric: str, limit: int, api_key: str, quota: Quota) -> list[dict]:
    limit = min(limit, RANKING_MAX_LIMIT)
    payload = api_get(f"/rankings/{metric_path(metric)}", {"limit": limit},
                      api_key, quota)
    if "error" in payload:
        msg = payload["error"].get("message", "")
        print(f"  警告: rankings/{metric_path(metric)} 取得失敗 — {msg[:160]}",
              file=sys.stderr)
        return []
    return payload.get("data", [])


def passes(value: float | None, lo, hi) -> bool:
    if value is None:
        return False
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def latest_ratios(payload: dict) -> dict:
    """ratios の時系列から最新期を取り、% 系を100倍してランキングと単位を揃える。"""
    rows = payload.get("data") or []
    if not rows:
        return {}
    latest = max(rows, key=lambda r: r.get("fiscal_year") or 0)
    out = {}
    for k, v in latest.items():
        if isinstance(v, (int, float)) and k in RATIO_PCT_FIELDS:
            out[k] = v * 100
        else:
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="criteria/*.json")
    ap.add_argument("--outdir", default="results/01_screen")
    ap.add_argument("--quota", type=int, default=DAILY_QUOTA)
    ap.add_argument("--limit", type=int, default=RANKING_MAX_LIMIT)
    ap.add_argument("--enrich", action="store_true",
                    help="通過銘柄の ratios を追加取得 (1銘柄1req)。連続増配年数などが付く")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    crit = json.loads(Path(args.config).read_text())
    require: dict = crit.get("require", {})
    score: dict = crit.get("score", {})
    metrics = list(require) + [m for m in score if m not in require]

    if args.dry_run:
        print(f"rankings: {len(metrics)} req ({', '.join(metric_path(m) for m in metrics)})")
        print(f"enrich  : 通過銘柄数ぶん (--enrich 指定時のみ)")
        print(f"合計    : 最低 {len(metrics)} req / {args.quota}")
        return 0

    api_key = os.environ.get("EDINETDB_API_KEY")
    if not api_key:
        print("EDINETDB_API_KEY が未設定。https://edinetdb.jp/developers で取得",
              file=sys.stderr)
        return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(os.path.expanduser(args.outdir))
    outdir.mkdir(parents=True, exist_ok=True)
    quota = Quota(args.quota)

    # --- ランキングを集めて銘柄ごとの指標テーブルを作る ---------------------
    table: dict[str, dict] = {}
    meta_info: dict[str, dict] = {}
    for m in metrics:
        rows = fetch_ranking(m, args.limit, api_key, quota)
        if not rows:
            continue
        vals = [r["value"] for r in rows if isinstance(r.get("value"), (int, float))]
        meta_info[m] = {"n": len(rows), "unit": rows[0].get("unit"),
                        "best": vals[0] if vals else None,
                        "worst": vals[-1] if vals else None}
        print(f"rankings/{metric_path(m):26s} {len(rows):4d}件 "
              f"{meta_info[m]['best']} → {meta_info[m]['worst']} {meta_info[m]['unit'] or ''}")
        for r in rows:
            e = r.get("edinet_code")
            if not e:
                continue
            rec = table.setdefault(e, {
                "edinet_code": e,
                "code": str(r.get("sec_code") or "")[:4],  # 4桁に正規化
                "name": r.get("name_ja") or r.get("name") or "",
                "industry": r.get("industry") or "",
            })
            rec[m] = r["value"]

    if not table:
        print("ランキングを1本も取得できませんでした。", file=sys.stderr)
        return 1

    # --- require を全部満たす銘柄だけ残し、score で加点 --------------------
    survivors = []
    for e, rec in table.items():
        if not all(passes(rec.get(m), *require[m]) for m in require):
            continue
        pts, hits = 0, []
        for m, (lo, hi) in score.items():
            if passes(rec.get(m), lo, hi):
                pts += 1
                hits.append(metric_path(m))
        rec["score"] = pts
        rec["score_hits"] = ";".join(hits)
        survivors.append(rec)

    survivors.sort(key=lambda r: (-r["score"], r.get(metrics[0]) or 0))

    cols = (["code", "name", "industry", "score", "score_hits", "edinet_code"]
            + [m for m in metrics])

    # --- 任意: 通過銘柄の ratios を足す ------------------------------------
    if args.enrich and survivors:
        extra = ["per", "pbr", "dividend_yield", "payout_ratio", "market_cap",
                 "consecutive_dividend_increase_years", "is_record_net_income",
                 "fiscal_year"]
        for rec in survivors:
            payload = api_get(f"/companies/{rec['edinet_code']}/ratios", {},
                              api_key, quota)
            if "error" in payload:
                continue
            latest = latest_ratios(payload)
            for k in extra:
                if k in latest:
                    rec[f"r_{k}"] = latest[k]
        cols += [f"r_{k}" for k in extra]

    csv_path = outdir / f"candidates_{ts}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(survivors)

    log = {
        "timestamp": ts,
        "config_file": args.config,
        "criteria": crit,
        "ranking_limit": min(args.limit, RANKING_MAX_LIMIT),
        "ranking_meta": meta_info,
        "universe_seen": len(table),
        "survivors": len(survivors),
        "api_requests_used": quota.used,
        "caveats": [
            "各ランキングは上位500件のみ。全上場約3,800社を網羅していない。",
            "よって『条件を満たす全銘柄』ではなく『各指標の上位500に同時に入る銘柄』。",
            "payout-ratio ランキングは配当性向が高い順。低配当性向の抽出には使えない。",
        ],
    }
    (outdir / f"screen_log_{ts}.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n観測ユニバース {len(table)} 社 → 通過 {len(survivors)} 社 "
          f"(API {quota.used} req)")
    for r in survivors[:20]:
        vals = " ".join(f"{metric_path(m)}={r[m]:g}" for m in metrics if m in r)
        print(f"  [{r['score']}] {r['code']} {r['name'][:20]:22s} {vals}")
    print(f"\n  {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
