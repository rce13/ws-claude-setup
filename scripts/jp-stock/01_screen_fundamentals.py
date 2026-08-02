#!/usr/bin/env python3
"""
目的: EDINET DB API で日本株の財務スクリーニングを行い、優待検討の候補を数十社に絞る。

入力:
  - 環境変数 EDINETDB_API_KEY
  - --config で指定する JSON (省略時は下の DEFAULT_CRITERIA)

出力:
  - {outdir}/candidates_{TS}.csv   絞り込み後の候補銘柄と財務指標
  - {outdir}/screen_log_{TS}.json  使用した条件・API消費数・除外理由の内訳
  - cache/                          API レスポンスキャッシュ (再実行で消費ゼロ)

注意:
  EDINET DB 無料プランは 100 リクエスト/日。全銘柄ループは不可能なので
  「rankings で粗く絞る → 生き残りにだけ ratios を叩く」という二段構えにしてある。
  キャッシュを消すと消費し直しになるので注意。

使い方:
  python 01_screen_fundamentals.py --outdir ~/projects/20260802_jpstock/results/01_screen
  python 01_screen_fundamentals.py --dry-run     # API を叩かず消費見積りだけ出す
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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
DAILY_QUOTA = 100  # Free プラン

# 「高いほど良い」指標のみ rankings で粗絞りに使う。
# 低いほど良い指標 (PBR/PER/de_ratio) は ranking の並び順仕様に依存させず、
# 生き残り銘柄の ratios を取ってからローカルで閾値判定する。
DEFAULT_CRITERIA = {
    "pool_metrics": ["dividend_yield", "roe", "equity_ratio"],
    "pool_limit": 300,          # 各 ranking から取る件数
    "pool_mode": "intersect",   # intersect | union
    "max_verify": 60,           # ratios を叩く上限 (= API 消費の上限)
    "thresholds": {
        # metric: [min, max]  (None は無制限)
        "roe": [8.0, None],
        "equity_ratio": [40.0, None],
        "pbr": [None, 2.0],
        "per": [None, 25.0],
        "dividend_yield": [1.5, None],
        "payout_ratio": [None, 80.0],
    },
}


class Quota:
    """API 消費数を数えて上限で止める。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def spend(self, n: int = 1) -> None:
        if self.used + n > self.limit:
            raise RuntimeError(
                f"API 消費上限 {self.limit} に到達 (used={self.used})。"
                "キャッシュを残したまま明日再実行するか --max-verify を下げてください。"
            )
        self.used += n


def api_get(path: str, params: dict, api_key: str, quota: Quota) -> dict:
    """GET + ディスクキャッシュ。キャッシュヒット時はクォータを消費しない。"""
    qs = urlencode(sorted(params.items()))
    key = hashlib.sha256(f"{path}?{qs}".encode()).hexdigest()[:16]
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{path.strip('/').replace('/', '_')}_{key}.json"

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    quota.spend()
    url = f"{BASE_URL}{path}?{qs}" if qs else f"{BASE_URL}{path}"
    req = Request(url, headers={"X-API-Key": api_key, "Accept": "application/json"})

    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            return data
        except HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code == 429:  # レート制限は待って再試行
                wait = 2 ** (attempt + 1)
                print(f"  429 rate limited, {wait}s 待機", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code} on {path}: {body}") from e
        except URLError as e:
            if attempt == 3:
                raise RuntimeError(f"接続失敗 {path}: {e}") from e
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"{path}: 再試行上限")


def extract_rows(payload) -> list[dict]:
    """レスポンス形状の揺れを吸収して行リストを返す。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "items", "results", "rankings", "companies"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
        # {"data": {"items": [...]}} のような入れ子
        for v in payload.values():
            if isinstance(v, dict):
                nested = extract_rows(v)
                if nested:
                    return nested
    return []


def get_code(row: dict) -> str | None:
    for k in ("code", "ticker", "sec_code", "securities_code", "edinet_code"):
        v = row.get(k)
        if v:
            return str(v).strip()
    return None


def get_name(row: dict) -> str:
    for k in ("name", "company_name", "filer_name", "name_ja"):
        v = row.get(k)
        if v:
            return str(v).strip()
    return ""


def as_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def passes(metrics: dict, thresholds: dict) -> tuple[bool, list[str]]:
    """閾値判定。値が取れない指標は「判定不能」として除外理由に残す。"""
    reasons = []
    for metric, (lo, hi) in thresholds.items():
        v = as_float(metrics.get(metric))
        if v is None:
            reasons.append(f"{metric}=欠損")
            continue
        if lo is not None and v < lo:
            reasons.append(f"{metric}={v:.2f}<{lo}")
        if hi is not None and v > hi:
            reasons.append(f"{metric}={v:.2f}>{hi}")
    return (not reasons, reasons)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="results/01_screen")
    ap.add_argument("--config", help="条件を書いた JSON")
    ap.add_argument("--max-verify", type=int, help="ratios を叩く上限を上書き")
    ap.add_argument("--quota", type=int, default=DAILY_QUOTA)
    ap.add_argument("--dry-run", action="store_true", help="API を叩かず消費見積りのみ")
    args = ap.parse_args()

    criteria = dict(DEFAULT_CRITERIA)
    if args.config:
        criteria.update(json.loads(Path(args.config).read_text()))
    if args.max_verify:
        criteria["max_verify"] = args.max_verify

    n_pool = len(criteria["pool_metrics"])
    if args.dry_run:
        print(f"rankings: {n_pool} req")
        print(f"ratios  : 最大 {criteria['max_verify']} req")
        print(f"合計    : 最大 {n_pool + criteria['max_verify']} req / {args.quota}")
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

    # --- 段階1: rankings で候補プールを作る -------------------------------
    pools: list[set[str]] = []
    names: dict[str, str] = {}
    for metric in criteria["pool_metrics"]:
        payload = api_get(f"/rankings/{metric}",
                          {"limit": criteria["pool_limit"]}, api_key, quota)
        rows = extract_rows(payload)
        if not rows:
            print(f"  警告: rankings/{metric} から行を抽出できず。"
                  f"レスポンス形状を確認: {str(payload)[:200]}", file=sys.stderr)
        codes = set()
        for r in rows:
            c = get_code(r)
            if c:
                codes.add(c)
                names.setdefault(c, get_name(r))
        pools.append(codes)
        print(f"rankings/{metric}: {len(codes)} 社")

    if not pools or not any(pools):
        print("候補プールが空。API レスポンス形状かキーを確認してください。", file=sys.stderr)
        return 1

    if criteria["pool_mode"] == "union":
        pool = set().union(*pools)
    else:
        pool = set(pools[0]).intersection(*pools[1:]) if len(pools) > 1 else pools[0]
    pool_sorted = sorted(pool)
    print(f"プール ({criteria['pool_mode']}): {len(pool_sorted)} 社")

    truncated = 0
    if len(pool_sorted) > criteria["max_verify"]:
        truncated = len(pool_sorted) - criteria["max_verify"]
        print(f"  注意: {truncated} 社は API 上限のため未検証のまま切り捨て", file=sys.stderr)
        pool_sorted = pool_sorted[: criteria["max_verify"]]

    # --- 段階2: 生き残りの ratios を取って閾値判定 ------------------------
    survivors, rejected = [], []
    for code in pool_sorted:
        try:
            payload = api_get(f"/companies/{code}/ratios", {}, api_key, quota)
        except RuntimeError as e:
            print(f"  {code}: {e}", file=sys.stderr)
            break
        rows = extract_rows(payload)
        metrics = rows[0] if rows else (payload if isinstance(payload, dict) else {})
        if isinstance(metrics.get("data"), dict):
            metrics = metrics["data"]

        ok, reasons = passes(metrics, criteria["thresholds"])
        rec = {"code": code, "name": names.get(code, get_name(metrics))}
        rec.update({m: as_float(metrics.get(m)) for m in criteria["thresholds"]})
        if ok:
            survivors.append(rec)
        else:
            rejected.append({**rec, "reasons": "; ".join(reasons)})

    # --- 出力 -------------------------------------------------------------
    cols = ["code", "name"] + list(criteria["thresholds"])
    csv_path = outdir / f"candidates_{ts}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(sorted(survivors, key=lambda r: -(r.get("dividend_yield") or 0)))

    log = {
        "timestamp": ts,
        "criteria": criteria,
        "api_requests_used": quota.used,
        "pool_size": len(pool),
        "truncated_by_quota": truncated,
        "survivors": len(survivors),
        "rejected": len(rejected),
        "rejected_detail": rejected,
    }
    (outdir / f"screen_log_{ts}.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2))

    print(f"\n通過 {len(survivors)} 社 / 検証 {len(survivors) + len(rejected)} 社 "
          f"(API {quota.used} req 消費)")
    print(f"  {csv_path}")
    if truncated:
        print(f"  ※ クォータ上限で {truncated} 社は未検証。翌日再実行すると"
              f"キャッシュ済みは消費せず続きから進みます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
