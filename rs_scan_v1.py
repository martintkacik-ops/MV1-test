"""
rs_scan_v1.py  --  Relative-strength scanner built on stockdex daily prices.

For a list of tickers vs a benchmark (default SPY) it computes:
  * Returns over 1W / 1M / 3M / 6M / 12M (5/21/63/126/252 trading days)
  * Relative return vs benchmark for each window
  * Cross-sectional percentile rank (0-100) of each window within YOUR list
    (same idea as PERCENTRANK in Sheets)
  * RS_MULTI: weighted average of the window ranks (weights editable below)
  * RS_IBD: IBD-style score = 0.4*3M + 0.2*6M + 0.2*9M + 0.2*12M returns,
    percentile-ranked 1-99 within your list
  * RS line (stock / benchmark): % below its 52-week high, new-high flag,
    and whether it is above its own 50-day average

Note: percentile ranks are relative to the list you pass in, so a small list
gives coarse ranks. Use 30+ tickers for meaningful rankings.
Prices are Yahoo daily closes as returned by stockdex (not dividend adjusted).

Install:  pip install "stockdex==1.6.0"
Usage:
  python rs_scan_v1.py NVDA AAPL MSFT AMD --bench SPY
  python rs_scan_v1.py --file watchlist.txt --out rs.csv
  python rs_scan_v1.py --file watchlist.txt --sort RS_IBD
  python rs_scan_v1.py --line NVDA --bench SPY --out nvda_rs_line.csv
watchlist.txt: one ticker per line, or comma/space separated; '#' = comment.
"""

import argparse
import sys
import time

import numpy as np
import pandas as pd

WINDOWS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
# Weights for RS_MULTI (must sum to 1). Edit to taste.
MULTI_WEIGHTS = {"1W": 0.10, "1M": 0.20, "3M": 0.30, "6M": 0.25, "12M": 0.15}
# IBD-style quarters: (lookback days, weight)
IBD_PARTS = [(63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)]


# ---------------------------------------------------------------- data ----

def fetch_close(symbol, range_="2y", retries=2, pause=2.0):
    """Daily close series indexed by date, via stockdex."""
    from stockdex import Ticker  # imported here so the math can be tested offline

    last = None
    for i in range(retries + 1):
        try:
            df = Ticker(ticker=symbol).yahoo_api_price(range=range_, dataGranularity="1d")
            df = df[["timestamp", "close"]].copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.dropna()
            s = pd.Series(df["close"].values, index=df["timestamp"].dt.normalize())
            s = s[~s.index.duplicated(keep="last")].sort_index()
            if len(s) < 30:
                raise ValueError("too few price rows")
            return s
        except Exception as e:  # blocked / rate limited / bad symbol
            last = e
            time.sleep(pause * (i + 1))
    raise RuntimeError(f"{symbol}: {last}")


def load_prices(symbols, bench, range_="2y", sleep=0.5):
    closes, failed = {}, []
    for sym in [bench] + [s for s in symbols if s != bench]:
        try:
            closes[sym] = fetch_close(sym, range_)
            print(f"  ok   {sym} ({len(closes[sym])} bars)", file=sys.stderr)
        except Exception as e:
            failed.append(sym)
            print(f"  FAIL {sym}: {e}", file=sys.stderr)
        time.sleep(sleep)
    if bench not in closes:
        raise RuntimeError(f"Benchmark {bench} failed to load; cannot compute RS.")
    px = pd.DataFrame(closes).sort_index()
    px = px.ffill(limit=3)
    return px, failed


# ---------------------------------------------------------------- math ----

def pct_rank(s):
    """0-100 percentile rank like PERCENTRANK: (count below) / (n - 1)."""
    s = s.astype(float)
    n = s.notna().sum()
    if n < 2:
        return s * np.nan
    r = s.rank(method="min")
    return (r - 1) / (n - 1) * 100.0


def ret_over(px, days):
    """Return over `days` bars using the last valid prices."""
    if len(px) <= days:
        return pd.Series(np.nan, index=px.columns)
    return px.iloc[-1] / px.iloc[-1 - days] - 1.0


def compute(px, bench):
    """px: DataFrame of closes (columns = tickers incl. bench)."""
    names = [c for c in px.columns if c != bench]
    out = pd.DataFrame(index=names)

    # window returns and relative returns
    for label, d in WINDOWS.items():
        r = ret_over(px, d)
        out[f"R_{label}"] = r[names] * 100.0
        out[f"REL_{label}"] = (r[names] - r[bench]) * 100.0

    # cross-sectional ranks of RELATIVE return (equal to rank of raw return
    # when everything shares one benchmark, kept relative for clarity)
    for label in WINDOWS:
        out[f"RK_{label}"] = pct_rank(out[f"REL_{label}"])

    w = pd.Series(MULTI_WEIGHTS)
    rk = out[[f"RK_{k}" for k in MULTI_WEIGHTS]]
    rk.columns = list(MULTI_WEIGHTS)
    # renormalise weights per row if some windows are missing (short history)
    wmat = rk.notna().mul(w, axis=1)
    out["RS_MULTI"] = (rk.fillna(0).mul(w, axis=1).sum(axis=1) / wmat.sum(axis=1)).round(1)

    # IBD-style weighted score, ranked 1-99
    score = 0
    for d, wt in IBD_PARTS:
        score = score + ret_over(px, d)[names] * wt
    out["IBD_SCORE"] = score * 100.0
    ibd = pct_rank(out["IBD_SCORE"])
    out["RS_IBD"] = (1 + ibd * 0.98).round(0)

    # RS line stats
    line = px[names].div(px[bench], axis=0)
    last = line.iloc[-1]
    hi52 = line.tail(252).max()
    out["RSLINE_VS_52W_HI_%"] = (last / hi52 - 1.0) * 100.0
    out["RSLINE_NEW_HIGH"] = last >= hi52 * 0.9999
    out["RSLINE_ABOVE_50MA"] = last > line.rolling(50).mean().iloc[-1]
    # rising 4 weeks
    out["RSLINE_UP_20D"] = last > line.iloc[-21] if len(line) > 21 else np.nan

    out.index.name = "Ticker"
    return out.round(2)


def rs_line_series(px, bench, symbol):
    s = px[symbol] / px[bench]
    d = pd.DataFrame({"RS_LINE": s, "RS_50MA": s.rolling(50).mean(),
                      "RS_252_HIGH": s.rolling(252, min_periods=20).max()})
    d.index.name = "Date"
    return d.dropna(how="all")


# ----------------------------------------------------------------- cli ----

def read_symbols(args):
    syms = [s.upper() for s in args.tickers]
    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.split("#")[0]
                syms += [t.strip().upper() for t in line.replace(",", " ").split() if t.strip()]
    seen, uniq = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tickers", nargs="*")
    p.add_argument("--file", help="watchlist text file")
    p.add_argument("--bench", default="SPY")
    p.add_argument("--range", default="2y", help="price history to load (2y default, needed for 12M)")
    p.add_argument("--sort", default="RS_MULTI", help="column to sort by (descending)")
    p.add_argument("--out", help="CSV output path")
    p.add_argument("--line", help="print/save RS line series for this ticker instead of the scan")
    p.add_argument("--sleep", type=float, default=0.5, help="seconds between requests")
    a = p.parse_args(argv)

    bench = a.bench.upper()
    if a.line:
        sym = a.line.upper()
        px, _ = load_prices([sym], bench, a.range, a.sleep)
        res = rs_line_series(px, bench, sym)
    else:
        syms = read_symbols(a)
        if not syms:
            p.print_help()
            return 0
        px, failed = load_prices(syms, bench, a.range, a.sleep)
        res = compute(px, bench)
        if a.sort in res.columns:
            res = res.sort_values(a.sort, ascending=False)
        if failed:
            print(f"Failed symbols: {', '.join(failed)}", file=sys.stderr)

    if a.out:
        res.to_csv(a.out)
        print(f"saved {a.out}")
    else:
        with pd.option_context("display.max_columns", 60, "display.width", 250):
            print(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
