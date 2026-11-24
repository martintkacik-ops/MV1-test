#!/usr/bin/env python3
"""Pull Yahoo Finance data for everything in the page .html and write data.js  (v4: fixed prices/last/quotes missing from data.js).

Updates: table returns (Day..Year), top-bar pairs, futures, bonds, Home index charts (15m bars),
heat map and the Breadth page (S&P 500 + Nasdaq-100 universe). No account or key needed.
Run:  python fetch_yf_v3.py [page.html]   (default: the most recently modified .html in the folder)
To make another label updatable, add  "Label": "YAHOO_SYMBOL"  to QMAP in index.html.
"""
import json, os, re, sys, time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = Path(__file__).parent
OFFSETS = [1, 5, 21, 63, 126, 252]          # Day, Week, Month, 3M, 6M, Year (trading days)
CHART_SYMS = ["SPY", "QQQ", "DIA", "IWM"]   # Home charts use 15-minute bars
UA = {"User-Agent": "Mozilla/5.0"}
F = ("Open", "High", "Low", "Close", "Volume")
HISTORY_DAYS = 400   # calendar days of daily history (~275 trading days; the Year column needs 253)



# Last-resort Nasdaq-100 list (approximate) used only if Wikipedia can't be read and no cached list exists.
NDX_FALLBACK = ("AAPL MSFT NVDA AMZN META GOOGL GOOG TSLA AVGO COST NFLX AMD PEP ADBE CSCO TMUS LIN INTU QCOM TXN AMGN "
    "ISRG BKNG HON AMAT CMCSA PDD VRTX ARM PANW ADP GILD ADI MU SBUX LRCX MELI INTC KLAC MDLZ REGN CRWD SNPS APP PYPL CDNS "
    "CTAS MAR ORLY MRVL ASML ABNB CSX FTNT ADSK DASH WDAY CEG CHTR ROP NXPI PCAR AEP CPRT MNST AZN FAST KDP PAYX TTD ROST "
    "ODFL IDXX EA VRSK EXC FANG XEL CTSH CSGP DDOG KHC LULU GEHC TEAM BKR CCEP ON CDW DXCM TTWO BIIB GFS MDB ZS WBD PLTR "
    "AXON MSTR SHOP TRI INSM").split()


def start_date():
    return (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()


def latest_html(name=None):
    """The page to read tickers from: the given name, else the newest .html in this folder."""
    if name:
        return HERE / name
    pages = sorted(HERE.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pages:
        raise FileNotFoundError("No .html file found next to this script")
    return pages[0]


def num(v, nd=2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


# ---------- what to fetch ----------
def extra_tickers():
    """Tickers you added in the Watchlists page: list them (space/comma/line separated) in extra_tickers.txt."""
    f = HERE / "extra_tickers.txt"
    if not f.exists():
        return []
    words = re.split(r"[\s,]+", re.sub(r"#.*", "", f.read_text()))
    return sorted({w.upper().lstrip("$") for w in words if re.fullmatch(r"\$?[A-Za-z0-9.\-^=]{1,10}", w)})


def page_symbols(html):
    tables = set(re.findall(r"\['([A-Z][A-Z0-9.\-]*)','", html))
    qmap = json.loads(re.search(r"const QMAP=(\{.*?\});", html, re.S).group(1))
    heat = set(re.findall(r"\['([A-Z][A-Z.\-]*)',", re.search(r"const SEC=\{(.*?)\};", html, re.S).group(1)))
    return sorted(tables), qmap, sorted(heat)


def wiki_tables(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return pd.read_html(StringIO(r.text))


def universe(kind):
    f = HERE / f"universe_{kind}.txt"
    try:
        if kind == "sp500":
            for t in wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"):
                if "Symbol" in t.columns:
                    return [x.replace(".", "-") for x in t["Symbol"].astype(str)]
        else:
            for t in wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100"):
                for c in ("Ticker", "Symbol"):
                    if c in t.columns and len(t) >= 80:
                        return [x.replace(".", "-") for x in t[c].astype(str)]
    except Exception as e:
        print(f"  could not read {kind} list from Wikipedia ({e})")
    if f.exists():
        return [x.strip() for x in f.read_text().split() if x.strip()]
    if kind != "sp500":
        print("  using the built-in Nasdaq-100 fallback list")
        return sorted(set(NDX_FALLBACK))
    return []


def download(symbols, **kw):
    out = {k: {} for k in F}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        df = None
        for attempt in range(3):          # Yahoo sometimes says "too many requests": wait and retry
            try:
                df = yf.download(chunk, auto_adjust=False, group_by="ticker", progress=False, threads=True, **kw)
                if df is not None and len(df):
                    break
            except Exception as e:
                print(f"  download retry {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
        if df is None or not len(df):
            continue
        for s in chunk:
            try:
                sub = df[s]
            except KeyError:
                continue
            for k in F:
                if k in sub:
                    out[k][s] = sub[k]
        print(f"  {min(i + 100, len(symbols))}/{len(symbols)}")
    return {k: pd.DataFrame(v).sort_index().dropna(how="all") for k, v in out.items()}


# ---------- simple pieces ----------
def returns(closes):
    if len(closes) < 2:
        return [None] * len(OFFSETS)
    last = closes[-1]
    return [num((last / closes[-1 - n] - 1) * 100) if len(closes) > n and closes[-1 - n] else None
            for n in OFFSETS]


def quotes(C, qmap):
    out = {}
    for label, sym in qmap.items():
        if sym in C.columns:
            s = C[sym].dropna()
            if len(s) >= 2:
                out[label] = {"p": num(s.iloc[-1], 4), "c": num(s.iloc[-1] - s.iloc[-2], 4),
                              "pc": num((s.iloc[-1] / s.iloc[-2] - 1) * 100)}
    return out


def intraday():
    hist = {}
    try:
        d = yf.download(CHART_SYMS, period="10d", interval="15m", auto_adjust=False,
                        group_by="ticker", progress=False, threads=True)
        for s in CHART_SYMS:
            sub = d[s].dropna(subset=["Close"]).tail(226)
            hist[s] = [[str(i), num(r.Open), num(r.High), num(r.Low), num(r.Close), int(r.Volume)]
                       for i, r in sub.iterrows()]
    except Exception as e:
        print("  intraday charts skipped:", e)
    return hist


# ---------- breadth ----------
def structure(C, O, cols):
    cols = [s for s in cols if s in C.columns]
    c, o = C[cols], O[cols]
    last = c.iloc[-1]
    sma = lambda n: c.rolling(n).mean()
    ema = lambda n: c.ewm(span=n, adjust=False).mean()
    e10, e20, s20, s50, s200 = ema(10), ema(20), sma(20), sma(50), sma(200)
    ref = lambda n: c.iloc[-1 - n]
    L = lambda m: m.iloc[-1]
    items = [("Day Chg", last > ref(1), ref(1)), ("Open Chg", last > o.iloc[-1], o.iloc[-1]),
             ("Week", last > ref(5), ref(5)), ("Month", last > ref(21), ref(21)),
             ("Qtr", last > ref(63), ref(63)), ("Half Year", last > ref(126), ref(126)),
             ("Year", last > ref(252), ref(252)),
             ("Price>EMA10", last > L(e10), L(e10)), ("Price>EMA20", last > L(e20), L(e20)),
             ("Price>SMA20", last > L(s20), L(s20)), ("Price>SMA50", last > L(s50), L(s50)),
             ("Price>SMA200", last > L(s200), L(s200)),
             ("Trend Days", L(s20) > s20.iloc[-6], s20.iloc[-6]),
             ("EMA10>=EMA20", L(e10) >= L(e20), L(e20)), ("SMA20>=SMA50", L(s20) >= L(s50), L(s50)),
             ("SMA50>=SMA200", L(s50) >= L(s200), L(s200)),
             ("20>=50>=200", (L(s20) >= L(s50)) & (L(s50) >= L(s200)), L(s200))]
    res = {}
    for label, cond, other in items:
        valid = last.notna() & other.notna()
        up = int((cond & valid).sum())
        res[label] = (int(valid.sum()) - up, up)
    return res


def breadth(C, O, H, L, V, sp, nd):
    P = [s for s in dict.fromkeys(sp + nd) if s in C.columns]
    if len(P) < 50:
        return None
    c, h, l, v = C[P], H[P], L[P], V[P]
    ret = c.pct_change()
    valid = ret.notna().sum(axis=1)
    up4, dn4 = (ret >= .04).sum(axis=1), (ret <= -.04).sum(axis=1)
    r5 = up4.rolling(5).sum() / dn4.rolling(5).sum().replace(0, np.nan)
    r10 = up4.rolling(10).sum() / dn4.rolling(10).sum().replace(0, np.nan)

    def mv(n, t):
        x = c / c.shift(n) - 1
        return (x >= t).sum(axis=1), (x <= -t).sum(axis=1)

    u25q, d25q = mv(63, .25)
    u25m, d25m = mv(21, .25)
    u50m, d50m = mv(21, .50)
    dates = c.index[-6:][::-1]
    S = [[d.strftime("%m/%d"), int(up4[d]), int(dn4[d]), num(r5[d]), num(r10[d]), int(u25q[d]), int(d25q[d]),
          int(u25m[d]), int(d25m[d]), int(u50m[d]), int(d50m[d]), None, None, int(valid[d])] for d in dates]

    s5, s40, s50, s200 = (c.rolling(n).mean() for n in (5, 40, 50, 200))
    e20 = c.ewm(span=20, adjust=False).mean()
    pv = c.shift(1)
    atr = np.maximum(np.maximum(h - l, (h - pv).abs()), (l - pv).abs()).rolling(14).mean()
    ext = (((c - s50) / atr) > 7).sum(axis=1)
    hvc = ((v >= v.rolling(252, min_periods=60).max()) & (ret > 0)).sum(axis=1)

    def above(ma):
        return (c > ma).sum(axis=1) / (c.notna() & ma.notna()).sum(axis=1).replace(0, np.nan) * 100

    A = [above(m) for m in (s5, e20, s40, s50, s200)]
    U = [[d.strftime("%m-%d")] + [num(a[d], 1) for a in A] + [int(hvc[d]), int(ext[d])] for d in dates]

    sp_s, nd_s = structure(C, O, sp), structure(C, O, nd)
    I = [[k, sp_s[k][0], sp_s[k][1], nd_s[k][0], nd_s[k][1]] for k in sp_s]

    r = ret.iloc[-1]
    tot = int(r.notna().sum())
    adv, dec = int((r > 0).sum()), int((r < 0).sum())
    nh = int((c.iloc[-1] >= c.rolling(252, min_periods=200).max().iloc[-1]).sum())
    nl = int((c.iloc[-1] <= c.rolling(252, min_periods=200).min().iloc[-1]).sum())

    def split(a, b):
        t = a + b
        return (num(a / t * 100, 1) if t else 0, a, num(b / t * 100, 1) if t else 0, b)

    a50 = int((c.iloc[-1] > s50.iloc[-1]).sum()); b50 = int(((c.iloc[-1] <= s50.iloc[-1])).sum())
    a2 = int((c.iloc[-1] > s200.iloc[-1]).sum()); b2 = int(((c.iloc[-1] <= s200.iloc[-1])).sum())
    BB = [["Advancing", "Declining", num(adv / tot * 100, 1), adv, num(dec / tot * 100, 1), dec, ""],
          ["New High", "New Low", *split(nh, nl), ""],
          ["Above", "Below", *split(a50, b50), "SMA50"], ["Above", "Below", *split(a2, b2), "SMA200"]]
    return {"S": S, "U": U, "I": I, "BB": BB}


def internals(C, nd):
    cols = [s for s in nd if s in C.columns]
    if len(cols) < 50:
        return None
    c = C[cols]
    ret = c.pct_change()
    adv, dec = (ret > 0).sum(axis=1), (ret < 0).sum(axis=1)
    rana = (adv - dec) / (adv + dec).replace(0, np.nan) * 1000
    mco = rana.ewm(span=19, adjust=False).mean() - rana.ewm(span=39, adjust=False).mean()
    msi = mco.cumsum()
    sma10 = msi.rolling(10).mean()
    nh = (c >= c.rolling(252, min_periods=200).max()).sum(axis=1)
    nl = (c <= c.rolling(252, min_periods=200).min()).sum(axis=1)
    p5 = (c > c.rolling(5).mean()).sum(axis=1) / c.notna().sum(axis=1) * 100
    up4, dn4 = (ret >= .04).sum(axis=1), (ret <= -.04).sum(axis=1)
    g1 = np.log((up4 + 1) / (dn4 + 1)) * np.minimum(1, (up4 + dn4) / 10)   # damped until 10 movers
    g2 = (adv - dec) / (adv + dec).replace(0, np.nan)

    def norm(sr):  # today's value vs the prior 60 days
        w = sr.iloc[-61:-1].dropna()
        lo, hi = w.min(), w.max()
        return 0.5 if hi == lo else float(np.clip((sr.iloc[-1] - lo) / (hi - lo), 0, 1))

    t90 = lambda sr, nd_=2: [0.0 if pd.isna(x) else round(float(x), nd_) for x in sr.tail(90)]
    net = nh - nl
    return {"mc": t90(mco), "ms": t90(msi), "sma": t90(sma10), "hl": [int(x) for x in net.tail(90)],
            "p5": t90(p5, 1), "comp": [norm(g1), norm(g2), norm(p5 / 100), norm(mco)],
            "mco": float(mco.iloc[-1]), "msi": float(msi.iloc[-1]),
            "mcoTxt": f"{mco.iloc[-1]:+.2f}", "msiTxt": f"{msi.iloc[-1]:+.1f}",
            "hlTxt": f"{int(net.iloc[-1])} ({int(nh.iloc[-1])}H / {int(nl.iloc[-1])}L)",
            "p5Txt": f"{p5.iloc[-1]:.1f}%"}



# ---------- real ATR%, distance from 52-week high, RS ranks (replace the page's placeholder numbers) ----------
def ext_stats(C, H, L, Ce, tables, ref):
    """ATR%, % below 52w high, and RS ranks 1-99 against the S&P 500 + Nasdaq-100 stocks (the 'ref' list)."""
    refs = [s for s in ref if s in Ce.columns]
    if len(refs) < 50:
        return {}
    W = {"1": 21, "3": 63, "6": 126}
    IBD = [(63, .4), (126, .2), (189, .2), (252, .2)]

    def r_ref(n):
        return Ce[refs].iloc[-1] / Ce[refs].iloc[-1 - n] - 1 if len(Ce) > n else None

    def ibd(get):
        tot = wsum = 0
        for n, w in IBD:
            v = get(n)
            if v is not None and not pd.isna(v):
                tot += v * w
                wsum += w
        return tot / wsum if wsum else None

    refR = {n: r_ref(n) for n in (21, 63, 126, 189, 252)}
    refIBD = pd.Series({s: ibd(lambda n, s=s: None if refR[n] is None else refR[n][s]) for s in refs}).dropna()

    def rank(v, arr):
        arr = arr.dropna()
        if v is None or pd.isna(v) or not len(arr):
            return None
        return int(min(99, max(1, round(100 * (arr < v).sum() / len(arr)))))

    out = {}
    for t in tables:
        if t not in C.columns:
            continue
        df = pd.DataFrame({"h": H[t], "l": L[t], "c": C[t]}).dropna()
        if len(df) < 30:
            continue
        c = df["c"]
        pc = c.shift(1)
        tr = np.maximum(np.maximum(df["h"] - df["l"], (df["h"] - pc).abs()), (df["l"] - pc).abs())
        atr = tr.rolling(14).mean().iloc[-1]
        hi = df["h"].tail(252).max()
        ret = lambda n: (c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
        e = {"atr": num(atr / c.iloc[-1] * 100), "h52": num((c.iloc[-1] / hi - 1) * 100)}
        for k, n in W.items():
            e["rs" + k] = rank(ret(n), refR[n]) if refR[n] is not None else None
        e["rs"] = rank(ibd(ret), refIBD)
        out[t] = e
    return out

# ---------- main ----------
def main(html_name=None):
    page = latest_html(html_name)
    print(f"Reading tickers from {page.name}")
    html = page.read_text(encoding="utf-8")
    tables, qmap, heat = page_symbols(html)
    extra = extra_tickers()
    tables = sorted(set(tables) | set(extra))
    if extra:
        print(f"  + {len(extra)} extra tickers from extra_tickers.txt")
    print("Reading S&P 500 / Nasdaq-100 lists...")
    sp, nd = universe("sp500"), universe("ndx")
    print(f"  S&P 500: {len(sp)}  Nasdaq-100: {len(nd)}")
    allsyms = sorted(set(tables) | set(qmap.values()) | set(heat) | set(sp) | set(nd))
    print(f"Downloading {len(allsyms)} symbols (daily since {start_date()})...")
    D = download(allsyms, start=start_date(), interval="1d")
    C, O, H, L, V = D["Close"], D["Open"], D["High"], D["Low"], D["Volume"]
    if C.empty:
        raise SystemExit("Yahoo returned no data - keeping the old data.js")
    # Stock-only copy: drop days when only futures/crypto traded (they broke the 200-day averages before)
    spc = [s for s in sp if s in C.columns]
    ok = (C[spc].notna().mean(axis=1) > 0.5) if spc else C.notna().mean(axis=1) > 0.5
    Ce, Oe, He, Le, Ve = (x.loc[ok[ok].index] for x in (C, O, H, L, V))

    prices, last, missing = {}, {}, []
    for s in tables:
        cl = C[s].dropna().tolist() if s in C.columns else []
        if len(cl) < 2:
            missing.append(s)
        else:
            prices[s] = returns(cl)
            last[s] = num(cl[-1], 4)
    if len(prices) < 0.6 * len(tables) or "SPY" not in prices:
        raise SystemExit(f"Only {len(prices)} of {len(tables)} tickers came back - keeping the old data.js")
    q = quotes(C, qmap)
    hm = {s: num((C[s].dropna().iloc[-1] / C[s].dropna().iloc[-2] - 1) * 100)
          for s in heat if s in C.columns and C[s].dropna().size > 1}
    print("Downloading 15-minute bars for the Home charts...")
    out = {"asOf": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M"),   # Eastern time
           "prices": prices, "last": last, "quotes": q,
           "heat": hm, "hist": intraday()}
    try:
        out["ext"] = ext_stats(C, H, L, Ce, tables, list(dict.fromkeys(sp + nd)))
    except Exception as e:
        print("  ATR/52wH/RS skipped:", e)
    print("Computing breadth...")
    try:
        b = breadth(Ce, Oe, He, Le, Ve, sp, nd)
        if b:
            out["breadth"] = b
    except Exception as e:
        print("  breadth skipped:", e)
    try:
        i = internals(Ce, nd)
        if i:
            out["internals"] = i
        else:
            print("  internals skipped: fewer than 50 Nasdaq-100 stocks had data")
    except Exception as e:
        print("  internals skipped:", e)
    tmp = HERE / "data.js.tmp"   # write then swap, so the page never reads a half-written file
    tmp.write_text("window.MARKET_DATA = " + json.dumps(out) + ";\n", encoding="utf-8")
    os.replace(tmp, HERE / "data.js")
    print(f"Wrote data.js: {len(prices)} table tickers, {len(q)} quotes, {len(hm)} heat-map tickers"
          f"{', breadth' if 'breadth' in out else ''}{', internals' if 'internals' in out else ''}{', ext' if out.get('ext') else ''}")
    if missing:
        print("No data for:", ", ".join(missing))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
