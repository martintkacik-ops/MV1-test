"""
stockdex_data_v1.py  --  thin helper around the `stockdex` package (v1.6.0).

Covers: prices/quotes, statements, margins/ratios, quarterly fundamentals,
revenue breakdown, valuation/profile, analysts & earnings, dividends,
ETF/fund data.  Deliberately EXCLUDES ownership (holders, insider trades,
SEC filings) and options.

Install:   pip install "stockdex==1.6.0"
Usage:     python stockdex_data_v1.py NVDA earnings_reaction
           python stockdex_data_v1.py NVDA prices --range 1y --out nvda.csv
           python stockdex_data_v1.py --list
In code:   from stockdex_data_v1 import get
           df = get("NVDA", "margin_operating")

Data comes from free scraped sources (Yahoo, Macrotrends, Digrin, Finviz,
justETF) and can be delayed, rate-limited or blocked.  Not tested live in the
authoring sandbox (no outbound access) -- run a quick smoke test first.
"""

import argparse
import sys
import time

import pandas as pd
from stockdex import Ticker


# ---- each entry: name -> (description, function(ticker, **opts)) ----------

def _t(sym):
    return Ticker(ticker=sym)


def _prices(sym, range="1y", interval="1d", **_):
    # Yahoo supports intraday intervals too (1m..90m) for short ranges.
    return _t(sym).yahoo_api_price(range=range, dataGranularity=interval)


def _quotes(sym, **_):
    # sym may be "SPY,QQQ,NVDA"
    return Ticker.yahoo_api_quotes([s.strip() for s in sym.split(",")])


def _search(sym, limit=10, **_):
    return Ticker.yahoo_api_search(sym, limit=limit)


def _splits(sym, range="max", **_):
    return _t(sym).yahoo_api_splits(range=range)


def _stmt_yahoo(kind):
    def f(sym, freq="annual", **_):
        return getattr(_t(sym), f"yahoo_api_{kind}")(frequency=freq)
    return f


def _stmt_macro(kind):
    def f(sym, freq="annual", **_):
        return getattr(_t(sym), f"macrotrends_{kind}")(frequency=freq)
    return f


def _prop(name):
    def f(sym, **_):
        return getattr(_t(sym), name)
    return f


def _meth(name, **fixed):
    def f(sym, **_):
        return getattr(_t(sym), name)(**fixed)
    return f


def _dict_to_df(name):
    def f(sym, **_):
        d = getattr(_t(sym), name)()
        return pd.DataFrame(d) if isinstance(d, dict) else d
    return f


def _valuation(sym, freq="quarterly", **_):
    return _t(sym).yahoo_api_valuation_measures(frequency=freq)


def _dividends_yahoo(sym, range="max", **_):
    return _t(sym).yahoo_api_dividends(range=range)


def _etf_profile(isin, **_):
    t = Ticker(isin=isin, security_type="etf")
    return {
        "basics": t.justetf_basics,
        "countries": t.justetf_holdings_countries,
        "sectors": t.justetf_holdings_sectors,
        "top_holdings": t.justetf_holdings_companies,
    }


DATASETS = {
    # prices & quotes
    "prices": ("Daily OHLCV (opts: --range, --interval)", _prices),
    "quotes": ("Live quotes; pass comma list e.g. SPY,QQQ,NVDA", _quotes),
    "search": ("Find tickers by name", _search),
    "splits": ("Stock split history", _splits),
    # statements (Yahoo ~5y, Macrotrends ~15y)
    "income_yahoo": ("Income statement, Yahoo (--freq annual|quarterly)", _stmt_yahoo("income_statement")),
    "cashflow_yahoo": ("Cash flow, Yahoo", _stmt_yahoo("cash_flow")),
    "balance_yahoo": ("Balance sheet, Yahoo", _stmt_yahoo("balance_sheet")),
    "financials_yahoo": ("EBITDA/EBIT etc., Yahoo", _stmt_yahoo("financials")),
    "income_macro": ("Income statement, Macrotrends (~15y)", _stmt_macro("income_statement")),
    "cashflow_macro": ("Cash flow, Macrotrends", _stmt_macro("cash_flow")),
    "balance_macro": ("Balance sheet, Macrotrends", _stmt_macro("balance_sheet")),
    "revenue_macro": ("Revenue history, Macrotrends", _stmt_macro("revenue")),
    # margins & ratios (Macrotrends)
    "margin_gross": ("Gross margin history", _prop("macrotrends_gross_margin")),
    "margin_operating": ("Operating margin history", _prop("macrotrends_operating_margin")),
    "margin_ebitda": ("EBITDA margin history", _prop("macrotrends_ebitda_margin")),
    "margin_pretax": ("Pre-tax margin history", _prop("macrotrends_pre_tax_margin")),
    "margin_net": ("Net margin history", _prop("macrotrends_net_margin")),
    "key_ratios": ("Key financial ratios", _prop("macrotrends_key_financial_ratios")),
    # quarterly fundamentals (Digrin)
    "q_assets_liabilities": ("Assets vs liabilities (quarterly)", _prop("digrin_assets_vs_liabilities")),
    "q_cash_debt": ("Cash and debt (quarterly)", _prop("digrin_cash_and_debt")),
    "q_cost_of_revenue": ("Cost of revenue", _prop("digrin_cost_of_revenue")),
    "q_expenses": ("Expenses", _prop("digrin_expenses")),
    "q_fcf": ("Free cash flow", _prop("digrin_free_cash_flow")),
    "q_net_income": ("Net income", _prop("digrin_net_income")),
    "q_shares": ("Shares outstanding", _prop("digrin_shares_outstanding")),
    # revenue breakdown (Finviz)
    "rev_segment": ("Revenue by segment", _dict_to_df("finviz_revenue_by_segment")),
    "rev_region": ("Revenue by region", _dict_to_df("finviz_revenue_by_regions")),
    "rev_product": ("Revenue by product/service", _dict_to_df("finviz_revenue_by_products_and_services")),
    # valuation & company
    "key_stats": ("Market cap, P/E, beta, 52w range, targets", _meth("yahoo_api_key_statistics")),
    "valuation": ("Valuation ratios over time (--freq annual|quarterly|trailing)", _valuation),
    "profile": ("Company profile", _meth("yahoo_api_profile")),
    "officers": ("Key executives", _meth("yahoo_api_officers")),
    "similar": ("Related tickers", _meth("yahoo_api_similar_symbols")),
    # analysts & earnings
    "estimates": ("Analyst EPS/revenue trend", _meth("yahoo_api_earnings_trend")),
    "recommendations": ("Analyst recommendation trend", _meth("yahoo_api_recommendation_trend")),
    "upgrades_downgrades": ("Rating changes", _meth("yahoo_api_upgrades_downgrades")),
    "earnings_history": ("EPS actual vs estimate (Yahoo)", _meth("yahoo_api_earnings_history")),
    "earnings_calendar": ("Next earnings / ex-div / pay dates", _meth("yahoo_api_calendar_events")),
    "earnings_reaction": ("Price reaction to earnings (Finviz)", _meth("finviz_price_reaction_to_earnings_report")),
    "earnings_data": ("EPS/revenue surprises (Finviz)", _meth("finviz_earnings_data")),
    "earnings_revisions": ("Estimate revisions (Finviz)", _meth("finviz_earnings_revisions_data")),
    # dividends
    "dividends_yahoo": ("Dividend history, Yahoo", _dividends_yahoo),
    "dividends_digrin": ("Dividend history, Digrin", _prop("digrin_dividend")),
    "payout_ratio": ("Payout ratio, Digrin", _prop("digrin_payout_ratio")),
    "dividends_annual": ("Annual dividend/yield/payout, Finviz", _meth("finviz_dividends_annual_data")),
    # ETF / funds (Yahoo)
    "fund_holdings": ("ETF/fund top holdings", _meth("yahoo_api_fund_top_holdings")),
    "fund_sectors": ("ETF/fund sector weights", _meth("yahoo_api_fund_sector_weightings")),
    "fund_allocation": ("ETF/fund asset allocation", _meth("yahoo_api_fund_asset_allocation")),
    "fund_profile": ("Fund family, category, expense ratio", _meth("yahoo_api_fund_profile")),
    "fund_performance": ("Fund performance vs category", _meth("yahoo_api_fund_performance")),
    # European ETF by ISIN (pass the ISIN as the symbol)
    "etf_justetf": ("European ETF by ISIN (returns dict of frames)", _etf_profile),
}


def get(symbol, dataset, retries=2, pause=2.0, **opts):
    """Fetch one dataset with simple retry for transient rate limits."""
    if dataset not in DATASETS:
        raise KeyError(f"Unknown dataset '{dataset}'. Use --list.")
    fn = DATASETS[dataset][1]
    last = None
    for i in range(retries + 1):
        try:
            return fn(symbol, **opts)
        except Exception as e:  # provider blocks / layout changes / rate limits
            last = e
            time.sleep(pause * (i + 1))
    raise RuntimeError(f"{dataset} failed for {symbol}: {last}") from last


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbol", nargs="?")
    p.add_argument("dataset", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--range", default=None)
    p.add_argument("--interval", default=None)
    p.add_argument("--freq", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", help="CSV path (single DataFrame results only)")
    a = p.parse_args(argv)

    if a.list or not (a.symbol and a.dataset):
        for k, (d, _) in DATASETS.items():
            print(f"{k:22s} {d}")
        return 0

    opts = {k: v for k, v in dict(range=a.range, interval=a.interval, freq=a.freq, limit=a.limit).items() if v is not None}
    res = get(a.symbol, a.dataset, **opts)

    if isinstance(res, dict):
        for k, v in res.items():
            print(f"\n== {k} ==")
            print(v)
        return 0
    if a.out:
        res.to_csv(a.out)
        print(f"saved {a.out}")
    else:
        with pd.option_context("display.max_columns", 50, "display.width", 200):
            print(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
