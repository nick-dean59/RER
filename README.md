# Real Exchange Rate Explorer

A Streamlit app that computes the bilateral **real exchange rate (RER)** between any two IMF economies — plus the Euro area — using monthly IMF CPI and exchange-rate data from **January 1976** onward, where available.

## Quick start (Python 3.12)

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

## How to use it

1. **Choose Country 1 and Country 2** in the sidebar. The lists are searchable; type to filter. Use **⇅ Swap countries** to flip the quote direction.
2. **Pick the sample period** with the month slider. RER* (the sample average) and the Real Index are recalculated automatically whenever you move it.
3. **Choose the display**: Real, Nominal, or Both. "Both" puts the real index on the left axis and nominal S on the right. Turn on **Log scale** for currencies with very high inflation.
4. **Download CSV** to export every month in the selected window: inputs, intermediate values and results.

## Definitions

| Symbol | Meaning |
|---|---|
| E₁(t), E₂(t) | IMF exchange rates: national currency per **USD** (period average) |
| S(t) | Units of Country 2 currency per **one** unit of Country 1 currency = E₂(t) / E₁(t) |
| P₁(t), P₂(t) | Monthly CPI (all items, index) |
| RER(t) | S(t) × P₁(t) / P₂(t) |
| RER* | Average of RER(t) over **valid** months in the selected sample |
| Real Index(t) | 100 × RER(t) / RER* |

A month is **valid** only when all four inputs (two CPIs, two exchange rates) exist and are positive. Invalid months are excluded from RER* and shown as gaps in the chart. Nominal S is plotted in actual currency units and is not normalized.

**Interpretation:** a rising Real Index means Country 1's currency is appreciating in real terms against Country 2's. Above 100 means it is stronger than its average over the chosen sample.

## Data source

IMF SDMX 3.0 API: `https://api.imf.org/external/sdmx/3.0`

| Series | Dataflow | Key |
|---|---|---|
| CPI | `IMF.STA/CPI` | `{ISO3}.CPI._T.IX.M` |
| Exchange rate | `IMF.STA/ER` | `{ISO3}.XDC_USD.PA_RT.M` |

* The Euro area uses the IMF code **G163**.
* For the **United States**, E = 1 by definition, so no exchange-rate request is made.
* Responses are cached for 12 hours. **↻ Refresh IMF data** clears the cache.
* The app requests SDMX-JSON first and falls back to SDMX-CSV if the JSON layout is unexpected. Failed requests are retried with backoff.
* If the IMF requires a subscription key for your network, set it before launching: `export IMF_API_KEY=...`. It is sent as `Ocp-Apim-Subscription-Key`.

## Coverage caveats

IMF data have gaps, and some series start well after 1976. The **Data coverage & IMF series keys** panel lists the first and last month available for each of the four series.

Things to be aware of:

* **Euro area (G163):** the IMF publishes no `CPI` series for G163, so the app automatically falls back to the HICP (`G163.HICP._T.IX.M`). That series starts in December 1999. The same CPI → HICP fallback applies to any area whose CPI key comes back empty.
* **Euro members** (Germany, France, Croatia, …): the IMF's national exchange-rate series stop at euro adoption (e.g. Dec 1998 for Germany, Dec 2022 for Croatia). The app splices them into one continuous EUR-per-USD series. Earlier months are converted at the official irrevocable conversion rate (e.g. 1.95583 DEM per EUR). From adoption onward, the Euro-area rate is used. The data-coverage panel shows exactly how each series was built.
* **Redenominations** (e.g., Venezuela, Zimbabwe, Argentina, Türkiye) depend on how the IMF splices the series. Use the log scale and check the nominal S chart for breaks.
* The level of RER depends on CPI base years and is not meaningful on its own. Only its movement relative to RER* matters, which is why the Real Index is the series to interpret.
* If a country's IMF area code differs from the code in the built-in list, enter it under **Advanced: custom IMF area code** in the sidebar.

## Files

* `app.py`: the Streamlit application (data access, calculations, charts, UI)
* `requirements.txt`: dependencies
* `README.md`: this file
