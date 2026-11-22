# MarketVIBE (Yahoo data)

One time:  `py -m pip install -r requirements.txt`

Daily use: double-click **start.bat**. It refreshes all data (a minute or two), starts a small local server and opens
the dashboard at http://127.0.0.1:8765/index.html. Leave the black window open while you use it.
`start-fast.bat` skips the refresh and uses the last data. `refresh.bat` just rebuilds data.js and opens the file directly.

Watchlists: with the server running, a ticker you Add is priced right away and saved to `extra_tickers.txt`, so
future refreshes keep it up to date. Opening index.html directly (double-click) cannot do this, and the browser keeps
separate watchlists for the two ways of opening the page, so pick one (start.bat is recommended).

What gets updated: table prices and returns, top-bar pairs, futures and bonds, Home charts (15-minute bars),
the heat map, and the Breadth page (S&P 500 + Nasdaq-100 lists from Wikipedia).

Adding a header/futures/chart label: add `"Label": "YAHOO_SYMBOL"` to QMAP in index.html.

Notes: Yahoo data is unofficial and can be delayed or rate-limited. If a run fails, wait a few minutes or update:
`py -m pip install -U yfinance`. Breadth uses the S&P 500 / Nasdaq-100, not the whole market.
