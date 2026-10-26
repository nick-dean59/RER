"""
Real Exchange Rate Explorer
===========================

Streamlit app that computes the bilateral real exchange rate (RER) between any
two IMF economies (plus the Euro area) from monthly IMF CPI and exchange-rate
data, January 1976 onward where available.

Definitions
-----------
S(t)    units of Country 2 currency per 1 unit of Country 1 currency
P1, P2  monthly CPI of Country 1 and Country 2
RER(t)  = S(t) * P1(t) / P2(t)
RER*    = mean of RER(t) over valid months in the selected sample
Real Index(t) = 100 * RER(t) / RER*

IMF exchange rates are national currency per USD (E). Therefore
S(t) = E2(t) / E1(t)   (Country 2 per USD) / (Country 1 per USD).

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
IMF_BASE = "https://api.imf.org/external/sdmx/3.0"
IMF_AGENCY = "IMF.STA"
IMF_VERSION = "+"  # latest version of the dataflow
CPI_FLOW, CPI_KEY = "CPI", "{code}.CPI._T.IX.M"
HICP_KEY = "{code}.HICP._T.IX.M"  # fallback; the Euro area (G163) is published only as HICP
ER_FLOW, ER_KEY = "ER", "{code}.XDC_USD.PA_RT.M"

# Euro adopters: IMF national-currency-per-USD series stop at euro adoption.
# Before adoption we express them in euro-equivalent units using the irrevocable
# conversion rate (legacy units per 1 EUR); from adoption onward we use the
# Euro-area (G163) EUR-per-USD rate. The result is one continuous EUR-per-USD series.
EURO_ADOPTION: dict[str, tuple[str, float]] = {
    "AUT": ("1999-01", 13.7603), "BEL": ("1999-01", 40.3399), "DEU": ("1999-01", 1.95583),
    "ESP": ("1999-01", 166.386), "FIN": ("1999-01", 5.94573), "FRA": ("1999-01", 6.55957),
    "IRL": ("1999-01", 0.787564), "ITA": ("1999-01", 1936.27), "LUX": ("1999-01", 40.3399),
    "NLD": ("1999-01", 2.20371), "PRT": ("1999-01", 200.482), "GRC": ("2001-01", 340.750),
    "SVN": ("2007-01", 239.640), "CYP": ("2008-01", 0.585274), "MLT": ("2008-01", 0.429300),
    "SVK": ("2009-01", 30.1260), "EST": ("2011-01", 15.6466), "LVA": ("2014-01", 0.702804),
    "LTU": ("2015-01", 3.45280), "HRV": ("2023-01", 7.53450),
}

START_PERIOD = pd.Period("1976-01", freq="M")
CACHE_TTL_SECONDS = 12 * 60 * 60
REQUEST_TIMEOUT = 60

JSON_ACCEPT = "application/vnd.sdmx.data+json;version=2.0.0"
CSV_ACCEPT = "application/vnd.sdmx.data+csv;version=2.0.0"

EURO_AREA = "G163"
USA = "USA"

COLOR_REAL = "#0F766E"      # deep teal
COLOR_NOMINAL = "#C2410C"   # burnt orange
COLOR_BASE = "#94A3B8"      # slate (baseline)

# ISO3 / IMF area code -> (display name, currency code used for labels)
COUNTRIES: dict[str, tuple[str, str]] = {
    EURO_AREA: ("Euro area", "EUR"),
    "AFG": ("Afghanistan", "AFN"), "ALB": ("Albania", "ALL"), "DZA": ("Algeria", "DZD"),
    "AND": ("Andorra", "EUR"), "AGO": ("Angola", "AOA"), "ATG": ("Antigua and Barbuda", "XCD"),
    "ARG": ("Argentina", "ARS"), "ARM": ("Armenia", "AMD"), "ABW": ("Aruba", "AWG"),
    "AUS": ("Australia", "AUD"), "AUT": ("Austria", "EUR"), "AZE": ("Azerbaijan", "AZN"),
    "BHS": ("Bahamas", "BSD"), "BHR": ("Bahrain", "BHD"), "BGD": ("Bangladesh", "BDT"),
    "BRB": ("Barbados", "BBD"), "BLR": ("Belarus", "BYN"), "BEL": ("Belgium", "EUR"),
    "BLZ": ("Belize", "BZD"), "BEN": ("Benin", "XOF"), "BTN": ("Bhutan", "BTN"),
    "BOL": ("Bolivia", "BOB"), "BIH": ("Bosnia and Herzegovina", "BAM"),
    "BWA": ("Botswana", "BWP"), "BRA": ("Brazil", "BRL"), "BRN": ("Brunei Darussalam", "BND"),
    "BGR": ("Bulgaria", "BGN"), "BFA": ("Burkina Faso", "XOF"), "BDI": ("Burundi", "BIF"),
    "CPV": ("Cabo Verde", "CVE"), "KHM": ("Cambodia", "KHR"), "CMR": ("Cameroon", "XAF"),
    "CAN": ("Canada", "CAD"), "CAF": ("Central African Republic", "XAF"), "TCD": ("Chad", "XAF"),
    "CHL": ("Chile", "CLP"), "CHN": ("China", "CNY"), "COL": ("Colombia", "COP"),
    "COM": ("Comoros", "KMF"), "COD": ("Congo, Dem. Rep.", "CDF"), "COG": ("Congo, Rep.", "XAF"),
    "CRI": ("Costa Rica", "CRC"), "CIV": ("Côte d'Ivoire", "XOF"), "HRV": ("Croatia", "EUR"),
    "CUW": ("Curaçao", "ANG"), "CYP": ("Cyprus", "EUR"), "CZE": ("Czech Republic", "CZK"),
    "DNK": ("Denmark", "DKK"), "DJI": ("Djibouti", "DJF"), "DMA": ("Dominica", "XCD"),
    "DOM": ("Dominican Republic", "DOP"), "ECU": ("Ecuador", "USD"), "EGY": ("Egypt", "EGP"),
    "SLV": ("El Salvador", "USD"), "GNQ": ("Equatorial Guinea", "XAF"), "ERI": ("Eritrea", "ERN"),
    "EST": ("Estonia", "EUR"), "SWZ": ("Eswatini", "SZL"), "ETH": ("Ethiopia", "ETB"),
    "FJI": ("Fiji", "FJD"), "FIN": ("Finland", "EUR"), "FRA": ("France", "EUR"),
    "GAB": ("Gabon", "XAF"), "GMB": ("Gambia", "GMD"), "GEO": ("Georgia", "GEL"),
    "DEU": ("Germany", "EUR"), "GHA": ("Ghana", "GHS"), "GRC": ("Greece", "EUR"),
    "GRD": ("Grenada", "XCD"), "GTM": ("Guatemala", "GTQ"), "GIN": ("Guinea", "GNF"),
    "GNB": ("Guinea-Bissau", "XOF"), "GUY": ("Guyana", "GYD"), "HTI": ("Haiti", "HTG"),
    "HND": ("Honduras", "HNL"), "HKG": ("Hong Kong SAR", "HKD"), "HUN": ("Hungary", "HUF"),
    "ISL": ("Iceland", "ISK"), "IND": ("India", "INR"), "IDN": ("Indonesia", "IDR"),
    "IRN": ("Iran", "IRR"), "IRQ": ("Iraq", "IQD"), "IRL": ("Ireland", "EUR"),
    "ISR": ("Israel", "ILS"), "ITA": ("Italy", "EUR"), "JAM": ("Jamaica", "JMD"),
    "JPN": ("Japan", "JPY"), "JOR": ("Jordan", "JOD"), "KAZ": ("Kazakhstan", "KZT"),
    "KEN": ("Kenya", "KES"), "KIR": ("Kiribati", "AUD"), "KOR": ("Korea", "KRW"),
    "KOS": ("Kosovo", "EUR"), "KWT": ("Kuwait", "KWD"), "KGZ": ("Kyrgyz Republic", "KGS"),
    "LAO": ("Lao P.D.R.", "LAK"), "LVA": ("Latvia", "EUR"), "LBN": ("Lebanon", "LBP"),
    "LSO": ("Lesotho", "LSL"), "LBR": ("Liberia", "LRD"), "LBY": ("Libya", "LYD"),
    "LIE": ("Liechtenstein", "CHF"), "LTU": ("Lithuania", "EUR"), "LUX": ("Luxembourg", "EUR"),
    "MAC": ("Macao SAR", "MOP"), "MDG": ("Madagascar", "MGA"), "MWI": ("Malawi", "MWK"),
    "MYS": ("Malaysia", "MYR"), "MDV": ("Maldives", "MVR"), "MLI": ("Mali", "XOF"),
    "MLT": ("Malta", "EUR"), "MHL": ("Marshall Islands", "USD"), "MRT": ("Mauritania", "MRU"),
    "MUS": ("Mauritius", "MUR"), "MEX": ("Mexico", "MXN"), "FSM": ("Micronesia", "USD"),
    "MDA": ("Moldova", "MDL"), "MNG": ("Mongolia", "MNT"), "MNE": ("Montenegro", "EUR"),
    "MAR": ("Morocco", "MAD"), "MOZ": ("Mozambique", "MZN"), "MMR": ("Myanmar", "MMK"),
    "NAM": ("Namibia", "NAD"), "NRU": ("Nauru", "AUD"), "NPL": ("Nepal", "NPR"),
    "NLD": ("Netherlands", "EUR"), "NZL": ("New Zealand", "NZD"), "NIC": ("Nicaragua", "NIO"),
    "NER": ("Niger", "XOF"), "NGA": ("Nigeria", "NGN"), "MKD": ("North Macedonia", "MKD"),
    "NOR": ("Norway", "NOK"), "OMN": ("Oman", "OMR"), "PAK": ("Pakistan", "PKR"),
    "PLW": ("Palau", "USD"), "PAN": ("Panama", "PAB"), "PNG": ("Papua New Guinea", "PGK"),
    "PRY": ("Paraguay", "PYG"), "PER": ("Peru", "PEN"), "PHL": ("Philippines", "PHP"),
    "POL": ("Poland", "PLN"), "PRT": ("Portugal", "EUR"), "QAT": ("Qatar", "QAR"),
    "ROU": ("Romania", "RON"), "RUS": ("Russia", "RUB"), "RWA": ("Rwanda", "RWF"),
    "KNA": ("St. Kitts and Nevis", "XCD"), "LCA": ("St. Lucia", "XCD"),
    "VCT": ("St. Vincent and the Grenadines", "XCD"), "WSM": ("Samoa", "WST"),
    "SMR": ("San Marino", "EUR"), "STP": ("São Tomé and Príncipe", "STN"),
    "SAU": ("Saudi Arabia", "SAR"), "SEN": ("Senegal", "XOF"), "SRB": ("Serbia", "RSD"),
    "SYC": ("Seychelles", "SCR"), "SLE": ("Sierra Leone", "SLE"), "SGP": ("Singapore", "SGD"),
    "SVK": ("Slovak Republic", "EUR"), "SVN": ("Slovenia", "EUR"),
    "SLB": ("Solomon Islands", "SBD"), "SOM": ("Somalia", "SOS"), "ZAF": ("South Africa", "ZAR"),
    "SSD": ("South Sudan", "SSP"), "ESP": ("Spain", "EUR"), "LKA": ("Sri Lanka", "LKR"),
    "SDN": ("Sudan", "SDG"), "SUR": ("Suriname", "SRD"), "SWE": ("Sweden", "SEK"),
    "CHE": ("Switzerland", "CHF"), "SYR": ("Syria", "SYP"), "TJK": ("Tajikistan", "TJS"),
    "TZA": ("Tanzania", "TZS"), "THA": ("Thailand", "THB"), "TLS": ("Timor-Leste", "USD"),
    "TGO": ("Togo", "XOF"), "TON": ("Tonga", "TOP"), "TTO": ("Trinidad and Tobago", "TTD"),
    "TUN": ("Tunisia", "TND"), "TUR": ("Türkiye", "TRY"), "TKM": ("Turkmenistan", "TMT"),
    "TUV": ("Tuvalu", "AUD"), "UGA": ("Uganda", "UGX"), "UKR": ("Ukraine", "UAH"),
    "ARE": ("United Arab Emirates", "AED"), "GBR": ("United Kingdom", "GBP"),
    "USA": ("United States", "USD"), "URY": ("Uruguay", "UYU"), "UZB": ("Uzbekistan", "UZS"),
    "VUT": ("Vanuatu", "VUV"), "VEN": ("Venezuela", "VES"), "VNM": ("Vietnam", "VND"),
    "PSE": ("West Bank and Gaza", "ILS"), "YEM": ("Yemen", "YER"), "ZMB": ("Zambia", "ZMW"),
    "ZWE": ("Zimbabwe", "ZWG"),
}


class IMFRequestError(RuntimeError):
    """Raised when the IMF API cannot be reached or returns an unusable response."""


# --------------------------------------------------------------------------- #
# IMF data access
# --------------------------------------------------------------------------- #
_PERIOD_RE = re.compile(r"^\s*(\d{4})-?M?(\d{1,2})(?!\d)")


def _to_period(value) -> pd.Period | None:
    """Parse IMF monthly period strings like '2020-M01', '2020M1', '2020-01'."""
    if value is None:
        return None
    m = _PERIOD_RE.match(str(value))
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return pd.Period(year=year, month=month, freq="M")


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _session() -> requests.Session:
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _headers(accept: str) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": "RealExchangeRateExplorer/1.0 (+streamlit)",
        "Cache-Control": "no-cache",
    }
    key = os.environ.get("IMF_API_KEY")  # optional subscription key
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    return headers


def _parse_sdmx_json(resp: requests.Response) -> pd.Series:
    """Parse an SDMX-JSON 2.x data message into a {Period: value} series."""
    payload = resp.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if "dataSets" not in data:
        raise ValueError("Unrecognised SDMX-JSON layout (no dataSets).")

    structures = data.get("structures") or ([data["structure"]] if data.get("structure") else [])
    values: dict[pd.Period, float] = {}

    for ds in data.get("dataSets") or []:
        s_idx = ds.get("structure", 0)
        s_idx = s_idx if isinstance(s_idx, int) and s_idx < len(structures) else 0
        struct = structures[s_idx] if structures else {}
        obs_dims = (struct.get("dimensions") or {}).get("observation") or []
        if not obs_dims:
            raise ValueError("SDMX-JSON message has no observation dimension.")
        t_pos = next(
            (i for i, d in enumerate(obs_dims) if d.get("id") in ("TIME_PERIOD", "TIME")), 0
        )
        t_values = [
            v.get("id") or v.get("value") or v.get("start") for v in obs_dims[t_pos].get("values", [])
        ]

        def add(obs_key: str, obs_val) -> None:
            parts = str(obs_key).split(":")
            try:
                t_idx = int(parts[t_pos] if len(parts) > t_pos else parts[-1])
                period = _to_period(t_values[t_idx])
            except (ValueError, IndexError):
                return
            raw = obs_val[0] if isinstance(obs_val, list) and obs_val else obs_val
            if isinstance(raw, list):
                raw = None
            x = _to_float(raw)
            if period is not None and x is not None:
                values.setdefault(period, x)

        series = ds.get("series")
        if series:
            for s in series.values():
                for k, v in (s.get("observations") or {}).items():
                    add(k, v)
        else:
            for k, v in (ds.get("observations") or {}).items():
                add(k, v)

    return pd.Series(values, dtype=float)


def _parse_sdmx_csv(resp: requests.Response) -> pd.Series:
    text = resp.text.strip()
    if not text:
        return pd.Series(dtype=float)
    df = pd.read_csv(io.StringIO(text), dtype=str)
    cols = {c.upper(): c for c in df.columns}
    if "TIME_PERIOD" not in cols or "OBS_VALUE" not in cols:
        raise ValueError("SDMX-CSV response lacks TIME_PERIOD/OBS_VALUE columns.")
    values: dict[pd.Period, float] = {}
    for t, v in zip(df[cols["TIME_PERIOD"]], df[cols["OBS_VALUE"]]):
        p, x = _to_period(t), _to_float(v)
        if p is not None and x is not None:
            values.setdefault(p, x)
    return pd.Series(values, dtype=float)


def _finalize(s: pd.Series) -> pd.Series:
    if s.empty:
        return pd.Series(dtype=float, index=pd.PeriodIndex([], freq="M"))
    s = s[~s.index.duplicated(keep="first")].sort_index()
    s.index = pd.PeriodIndex(s.index, freq="M")
    s = s[s.index >= START_PERIOD]
    return s.astype(float)


def fetch_imf_series(flow: str, key: str) -> pd.Series:
    """Fetch one monthly IMF series. Empty series = IMF has no data for the key.

    Raises IMFRequestError for network problems / unusable responses.
    """
    url = f"{IMF_BASE}/data/dataflow/{IMF_AGENCY}/{flow}/{IMF_VERSION}/{key}"
    params = {"dimensionAtObservation": "TIME_PERIOD", "attributes": "dsd", "measures": "all"}
    last_error: str = "unknown error"

    for accept, parser in ((JSON_ACCEPT, _parse_sdmx_json), (CSV_ACCEPT, _parse_sdmx_csv)):
        try:
            resp = _session().get(url, params=params, headers=_headers(accept), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if resp.status_code in (204, 404):  # SDMX "no results found"
            return _finalize(pd.Series(dtype=float))
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            continue
        try:
            return _finalize(parser(resp))
        except Exception as exc:  # malformed / unexpected format -> try next format
            last_error = f"parse error ({type(exc).__name__}: {exc})"
            continue

    raise IMFRequestError(f"IMF request failed for {flow}/{key}: {last_error}")


def _fetch_price_index(code: str) -> tuple[pd.Series, str]:
    """CPI (all items); falls back to HICP when the IMF publishes no CPI for the area."""
    key = CPI_KEY.format(code=code)
    s = fetch_imf_series(CPI_FLOW, key)
    if s.empty:
        hicp_key = HICP_KEY.format(code=code)
        h = fetch_imf_series(CPI_FLOW, hicp_key)
        if not h.empty:
            return h, f"{hicp_key} (HICP)"
    return s, key


def _fetch_exchange_rate(code: str) -> tuple[pd.Series | None, str]:
    """National currency per USD. Euro adopters are spliced into one EUR-per-USD series."""
    if code == USA:
        return None, "— (USD = 1)"
    key = ER_KEY.format(code=code)
    national = fetch_imf_series(ER_FLOW, key)
    if code not in EURO_ADOPTION:
        return national, key

    adopt_str, conv = EURO_ADOPTION[code]
    adopt = pd.Period(adopt_str, freq="M")
    euro = fetch_imf_series(ER_FLOW, ER_KEY.format(code=EURO_AREA))
    pre = national[national.index < adopt] / conv          # legacy per USD -> EUR per USD
    post = euro[euro.index >= adopt]
    spliced = pd.concat([pre, post]).sort_index()
    desc = (f"{key} ÷ {conv:g} before {adopt.strftime('%b %Y')}, "
            f"then {ER_KEY.format(code=EURO_AREA)}")
    return spliced, desc


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_country_data(code: str) -> dict:
    """Price index and LCU-per-USD exchange rate for one IMF area code (cached)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cpi = pool.submit(_fetch_price_index, code)
        f_er = pool.submit(_fetch_exchange_rate, code)
        cpi, cpi_key = f_cpi.result()
        er, er_key = f_er.result()  # er None => USD itself (1 per USD)
    return {"cpi": cpi, "er": er, "cpi_key": cpi_key, "er_key": er_key}


# --------------------------------------------------------------------------- #
# Calculations
# --------------------------------------------------------------------------- #
def _positive(s: pd.Series | None, idx: pd.PeriodIndex) -> pd.Series:
    if s is None:
        return pd.Series(np.nan, index=idx)
    s = s.reindex(idx)
    return s.where(s > 0)


def build_frame(d1: dict, d2: dict) -> pd.DataFrame:
    """Monthly panel with E1, E2, S, P1, P2, RER and a validity flag."""
    pieces = [s for s in (d1["cpi"], d2["cpi"], d1["er"], d2["er"]) if s is not None and len(s)]
    if not pieces:
        return pd.DataFrame()
    end = max(s.index.max() for s in pieces)
    start = max(START_PERIOD, min(s.index.min() for s in pieces))
    idx = pd.period_range(start, end, freq="M")

    df = pd.DataFrame(index=idx)
    df["P1"] = _positive(d1["cpi"], idx)
    df["P2"] = _positive(d2["cpi"], idx)
    # USD itself: 1 USD per USD in every month.
    df["E1"] = 1.0 if d1["er"] is None else _positive(d1["er"], idx)
    df["E2"] = 1.0 if d2["er"] is None else _positive(d2["er"], idx)

    # S = (C2 per USD) / (C1 per USD) = C2 per 1 unit of C1
    df["S"] = df["E2"] / df["E1"]
    df["RER"] = df["S"] * df["P1"] / df["P2"]
    df["valid"] = np.isfinite(df[["S", "P1", "P2", "RER"]]).all(axis=1) & (df["RER"] > 0)
    df.loc[~df["valid"], "RER"] = np.nan
    return df


def sample_stats(win: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Add RER* and Real Index to the selected window."""
    win = win.copy()
    rer_star = float(win.loc[win["valid"], "RER"].mean())
    win["RER_star"] = rer_star
    win["REAL_INDEX"] = 100.0 * win["RER"] / rer_star
    return win, rer_star


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def fmt_num(x: float | None, sig: int = 4) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    ax = abs(x)
    if ax >= 1_000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    if ax == 0:
        return "0"
    decimals = max(sig, sig - int(math.floor(math.log10(ax))) - 1)
    return f"{x:.{decimals}f}"


def month_label(p: pd.Period) -> str:
    return p.strftime("%b %Y")


def country_label(code: str) -> str:
    name, cur = COUNTRIES.get(code, (code, code))
    return f"{name}  ·  {cur}"


def coverage(s: pd.Series | None) -> str:
    if s is None:
        return "USD (identity, = 1)"
    if s.empty:
        return "no data"
    return f"{month_label(s.index.min())} → {month_label(s.index.max())}  ({len(s):,} obs)"


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def make_chart(win: pd.DataFrame, view: str, s_label: str, pair_title: str, log_nominal: bool) -> go.Figure:
    both = view == "Both"
    fig = make_subplots(specs=[[{"secondary_y": both}]])
    x = win.index.to_timestamp()

    if view in ("Real", "Both"):
        fig.add_trace(
            go.Scatter(
                x=x, y=np.full(len(x), 100.0), mode="lines", name="Sample average (100)",
                line=dict(color=COLOR_BASE, width=1.2, dash="dot"), hoverinfo="skip", showlegend=True,
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=x, y=win["REAL_INDEX"], mode="lines", name="Real index (RER* = 100)",
                line=dict(color=COLOR_REAL, width=2.4), connectgaps=False,
                hovertemplate="Real index: %{y:.2f}<extra></extra>",
            ),
            secondary_y=False,
        )

    if view in ("Nominal", "Both"):
        fig.add_trace(
            go.Scatter(
                x=x, y=win["S"], mode="lines", name=f"Nominal S ({s_label})",
                line=dict(color=COLOR_NOMINAL, width=2.0 if both else 2.4), connectgaps=False,
                hovertemplate="S: %{y:,.6~g}<extra></extra>",
            ),
            secondary_y=both,
        )

    axis_font = dict(size=12, color="#475569")
    fig.update_layout(
        template="plotly_white",
        height=540,
        margin=dict(l=10, r=10, t=60, b=10),
        font=dict(family="Inter, -apple-system, Segoe UI, Roboto, sans-serif", size=13, color="#0F172A"),
        title=dict(text=pair_title, x=0.0, xanchor="left", font=dict(size=16, color="#0F172A")),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", bordercolor="#E2E8F0", font_size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1.0, bgcolor="rgba(0,0,0,0)"),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_xaxes(
        showgrid=False, showline=True, linecolor="#CBD5E1", tickfont=axis_font,
        rangeselector=dict(
            buttons=[
                dict(count=5, label="5Y", step="year", stepmode="backward"),
                dict(count=10, label="10Y", step="year", stepmode="backward"),
                dict(count=20, label="20Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor="#F1F5F9", activecolor="#CCFBF1", font=dict(size=11), x=0, y=1.0, yanchor="bottom",
        ),
    )
    grid = dict(gridcolor="#EEF2F7", zeroline=False, tickfont=axis_font)

    if view == "Real":
        fig.update_yaxes(title_text="Real index (sample avg = 100)", **grid)
    elif view == "Nominal":
        fig.update_yaxes(title_text=s_label, type="log" if log_nominal else "linear", **grid)
    else:
        fig.update_yaxes(title_text="Real index (sample avg = 100)", title_font=dict(color=COLOR_REAL),
                         secondary_y=False, **grid)
        fig.update_yaxes(title_text=f"Nominal S: {s_label}", title_font=dict(color=COLOR_NOMINAL),
                         type="log" if log_nominal else "linear", showgrid=False, zeroline=False,
                         tickfont=axis_font, secondary_y=True)
    return fig


# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, select, textarea {
    font-family: 'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif;
}
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1280px; }
.hero {
    background: linear-gradient(120deg, #0B1F33 0%, #0F3D4C 55%, #0F766E 100%);
    color: #F8FAFC; border-radius: 18px; padding: 1.6rem 1.9rem 1.4rem; margin-bottom: 1.2rem;
    box-shadow: 0 10px 30px rgba(15, 61, 76, 0.18);
}
.hero h1 { color: #F8FAFC; font-size: 1.85rem; font-weight: 700; margin: 0 0 .25rem 0; letter-spacing: -0.02em; }
.hero p { color: #CBD5E1; margin: 0; font-size: 0.98rem; }
.hero .pair { display: inline-block; margin-top: .85rem; padding: .3rem .8rem; border-radius: 999px;
    background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.18); font-size: .9rem; color: #E2E8F0; }
div[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px; padding: .9rem 1rem;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
div[data-testid="stMetricLabel"] p { color: #64748B; font-size: .82rem; font-weight: 500; }
div[data-testid="stMetricValue"] { color: #0F172A; font-size: 1.45rem; font-weight: 600; }
section[data-testid="stSidebar"] { background: #F8FAFC; border-right: 1px solid #E2E8F0; }
.sidebar-h { font-size: .74rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
    color: #64748B; margin: .6rem 0 .2rem 0; }
.note { color: #64748B; font-size: .86rem; }
.stDownloadButton button { background: #0F766E; color: white; border: 0; border-radius: 10px; font-weight: 600; }
.stDownloadButton button:hover { background: #115E59; color: white; }
</style>
"""


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
def _swap() -> None:
    st.session_state["c1"], st.session_state["c2"] = st.session_state["c2"], st.session_state["c1"]
    st.session_state["c1_override"], st.session_state["c2_override"] = (
        st.session_state.get("c2_override", ""), st.session_state.get("c1_override", ""),
    )


def _resolve(code_key: str, override_key: str) -> str:
    override = (st.session_state.get(override_key) or "").strip().upper()
    return override or st.session_state[code_key]


def main() -> None:
    st.set_page_config(page_title="Real Exchange Rate Explorer", page_icon="💱", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    codes = sorted(COUNTRIES, key=lambda c: (c != EURO_AREA, COUNTRIES[c][0]))
    st.session_state.setdefault("c1", USA)
    st.session_state.setdefault("c2", EURO_AREA)

    # ---------------- Sidebar ----------------
    with st.sidebar:
        st.markdown('<div class="sidebar-h">Countries</div>', unsafe_allow_html=True)
        st.selectbox("Country 1 (base currency)", codes, key="c1", format_func=country_label,
                     help="S(t) is quoted per ONE unit of Country 1's currency.")
        st.button("⇅  Swap countries", on_click=_swap, width="stretch")
        st.selectbox("Country 2 (quote currency)", codes, key="c2", format_func=country_label)

        with st.expander("Advanced: custom IMF area code"):
            st.caption("Override a selection with any IMF area code (e.g. an aggregate). Leave blank to use the list.")
            st.text_input("Country 1 code", key="c1_override", placeholder="e.g. G163")
            st.text_input("Country 2 code", key="c2_override", placeholder="e.g. JPN")

        st.markdown('<div class="sidebar-h">Chart</div>', unsafe_allow_html=True)
        view = st.segmented_control("Series to display", ["Real", "Nominal", "Both"], default="Both",
                                    key="view") or "Both"
        log_nominal = st.toggle("Log scale for nominal S", value=False,
                                help="Useful for high-inflation currencies whose S spans orders of magnitude.")

        st.markdown('<div class="sidebar-h">Data</div>', unsafe_allow_html=True)
        if st.button("↻  Refresh IMF data", width="stretch"):
            get_country_data.clear()
            st.rerun()
        st.markdown('<p class="note">Source: IMF SDMX 3.0 API — CPI (all items, index) and '
                    'period-average exchange rates, monthly. Cached for 12 hours.</p>', unsafe_allow_html=True)

    c1, c2 = _resolve("c1", "c1_override"), _resolve("c2", "c2_override")
    name1, cur1 = COUNTRIES.get(c1, (c1, c1))
    name2, cur2 = COUNTRIES.get(c2, (c2, c2))
    # Disambiguate labels when both sides share a currency code (e.g. two euro members).
    lab1, lab2 = (cur1, cur2) if cur1 != cur2 else (f"{cur1} ({c1})", f"{cur2} ({c2})")
    s_label = f"{lab2} per 1 {lab1}"

    st.markdown(
        f"""<div class="hero"><h1>Real Exchange Rate Explorer</h1>
        <p>RER(t) = S(t) × P<sub>1</sub>(t) / P<sub>2</sub>(t), rebased to its average over the sample you choose.</p>
        <span class="pair">{name1} ({lab1}) &nbsp;→&nbsp; {name2} ({lab2})</span></div>""",
        unsafe_allow_html=True,
    )

    if c1 == c2:
        st.warning("Country 1 and Country 2 are the same — pick two different economies.")
        st.stop()

    # ---------------- Data ----------------
    try:
        with st.spinner(f"Fetching IMF data for {name1} and {name2}…"):
            d1 = get_country_data(c1)
            d2 = get_country_data(c2)
    except IMFRequestError as exc:
        st.error("Couldn't retrieve data from the IMF API. It may be temporarily unavailable or "
                 "rate-limiting requests — wait a moment and press **Refresh IMF data**.")
        with st.expander("Technical details"):
            st.code(str(exc))
        st.stop()

    df = build_frame(d1, d2)
    diag = pd.DataFrame(
        {
            "Series": [f"CPI — {name1}", f"CPI — {name2}", f"{cur1} per USD", f"{cur2} per USD"],
            "IMF key": [d1["cpi_key"], d2["cpi_key"], d1["er_key"], d2["er_key"]],
            "Coverage (from Jan 1976)": [coverage(d1["cpi"]), coverage(d2["cpi"]),
                                         coverage(d1["er"]), coverage(d2["er"])],
        }
    )

    if df.empty or not df["valid"].any():
        st.error(f"No month has all four inputs (both CPIs and both exchange rates) for "
                 f"**{name1}** and **{name2}**, so the real exchange rate can't be computed.")
        st.dataframe(diag, hide_index=True, width="stretch")
        st.stop()

    valid_months = df.index[df["valid"]]
    first, last = valid_months.min(), valid_months.max()
    options = list(pd.period_range(first, last, freq="M"))

    # ---------------- Sample selection ----------------
    start, end = st.select_slider(
        "Sample period — RER* is recalculated as the average over this window",
        options=options, value=(options[0], options[-1]), format_func=month_label,
        key=f"range_{c1}_{c2}",
    )
    win = df.loc[start:end]
    if not win["valid"].any():
        st.warning("No valid months in the selected window — widen the sample period.")
        st.stop()
    win, rer_star = sample_stats(win)

    n_total, n_valid = len(win), int(win["valid"].sum())
    vwin = win[win["valid"]]
    last_p = vwin.index.max()
    last_ri = float(vwin["REAL_INDEX"].iloc[-1])
    first_ri = float(vwin["REAL_INDEX"].iloc[0])
    s_valid = win["S"].dropna()
    last_s = float(s_valid.iloc[-1]) if not s_valid.empty else float("nan")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("RER* (sample average)", fmt_num(rer_star), help="Mean of RER(t) over valid months in the window.")
    m2.metric(f"Real index · {month_label(last_p)}", f"{last_ri:,.1f}",
              delta=f"{last_ri - 100:+.1f} vs. avg", delta_color="off")
    real_chg = round((last_ri / first_ri - 1) * 100, 1) + 0.0  # + 0.0 avoids "-0.0%"
    m3.metric("Real change over sample", f"{real_chg:+.1f}%",
              help="Latest real index vs. first valid month of the window.")
    m4.metric(f"Nominal S · {month_label(s_valid.index.max()) if not s_valid.empty else '—'}",
              fmt_num(last_s), help=s_label)
    m5.metric("Valid months", f"{n_valid:,} / {n_total:,}",
              help="Months with both CPIs and both exchange rates available.")

    if n_valid < n_total:
        st.caption(f"⚠️ {n_total - n_valid:,} month(s) in the window are missing at least one input; they are "
                   "excluded from RER* and shown as gaps in the real series.")

    # ---------------- Chart ----------------
    fig = make_chart(win, view, s_label, f"{name1} vs. {name2}", log_nominal)
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})

    direction = (f"A **rising** real index means {name1}'s currency is appreciating in real terms against "
                 f"{name2}'s — {name1}'s goods are becoming more expensive relative to {name2}'s. "
                 f"Above 100 means {lab1} is real-stronger than its {month_label(start)}–{month_label(end)} average.")
    st.markdown(f'<p class="note">{direction}</p>', unsafe_allow_html=True)

    # ---------------- Table + download ----------------
    out = pd.DataFrame(
        {
            "Month": win.index.strftime("%Y-%m"),
            f"CPI P1 ({name1})": win["P1"].to_numpy(),
            f"CPI P2 ({name2})": win["P2"].to_numpy(),
            f"E1: {lab1} per USD (IMF)": win["E1"].to_numpy(),
            f"E2: {lab2} per USD (IMF)": win["E2"].to_numpy(),
            f"S: {s_label}": win["S"].to_numpy(),
            "RER = S × P1 / P2": win["RER"].to_numpy(),
            "RER* (sample average)": win["RER_star"].to_numpy(),
            "Real index (100 × RER / RER*)": win["REAL_INDEX"].to_numpy(),
            "Valid month": win["valid"].to_numpy(),
        }
    )
    st.markdown("#### Observations")
    dl_col, info_col = st.columns([1, 3])
    with dl_col:
        st.download_button(
            "⬇  Download CSV",
            data=out.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"rer_{c1}_{c2}_{start.strftime('%Y%m')}_{end.strftime('%Y%m')}.csv",
            mime="text/csv",
            width="stretch",
        )
    with info_col:
        st.markdown(f'<p class="note">{n_total:,} monthly rows · {month_label(start)} – {month_label(end)} · '
                    f'all inputs and calculations included.</p>', unsafe_allow_html=True)

    with st.expander("View data table"):
        st.dataframe(out, hide_index=True, width="stretch", height=420)

    with st.expander("Data coverage & IMF series keys"):
        st.dataframe(diag, hide_index=True, width="stretch")

    with st.expander("Methodology"):
        st.markdown("IMF reports exchange rates as national currency per US dollar (E). "
                    "Converting to Country 2 currency per one unit of Country 1 currency:")
        st.latex(r"S(t) = \frac{E_2(t)}{E_1(t)} \qquad RER(t) = S(t)\,\frac{P_1(t)}{P_2(t)}")
        st.latex(r"RER^{*} = \frac{1}{N}\sum_{t \in \text{valid}} RER(t) \qquad "
                 r"\text{Real Index}(t) = 100\,\frac{RER(t)}{RER^{*}}")
        st.markdown("A month is **valid** only when all four inputs are available and positive. "
                    "Because CPI base years differ, the level of RER is not meaningful on its own — "
                    "only its movement relative to RER* is. Nominal S is shown in actual currency units.")
        st.markdown("**Euro members:** the IMF's national exchange-rate series end at euro adoption. "
                    "Earlier months are converted to euro-equivalent units at the official irrevocable "
                    "conversion rate (e.g. 1.95583 DEM per EUR), and later months use the Euro-area rate, "
                    "so nominal S is in euros throughout. **Euro area (G163)** uses the HICP, because the "
                    "IMF publishes no CPI series for it; it starts in Dec 1999.")


if __name__ == "__main__":
    main()
