#!/usr/bin/env python3
"""
Macro Dashboard Data Fetcher
GitHub Actions 每日自動執行，將所有指標數據存入 data.json
數據來源：fredapi (FRED) + yfinance (雅虎財經) + CoinGecko + NY Fed + US Treasury
"""
import json, os, sys, time, traceback
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np
import requests

try:
    from fredapi import Fred
except ImportError:
    print("請安裝: pip install fredapi"); sys.exit(1)

try:
    import yfinance as yf
except ImportError:
    print("請安裝: pip install yfinance"); sys.exit(1)

# ── 設定 ────────────────────────────────────────────────
FRED_KEY   = os.environ.get("FRED_API_KEY", "")
HIST_DAYS  = 730   # 圖表歷史天數（2年）
EXTRA_DAYS = 400   # YoY 計算所需額外歷史
START_DATE = (datetime.today() - timedelta(days=HIST_DAYS + EXTRA_DAYS)).strftime("%Y-%m-%d")
MAX_HIST   = 500   # 每個指標最多儲存的歷史分數點數

# ── 指標定義 ────────────────────────────────────────────
# (series_id, cn, unit, fav, freq, yf_ticker, fred_id, desc)
INDICATORS = [
  # 市場情緒
  ("VIXCLS",           "恐慌指數（波動率指標）",       "", "down", "daily",     "^VIX",      "VIXCLS",            "VIX < 20 情緒良好 | VIX > 30 恐慌 | 急拉代表重大事件"),
  # 商品
  ("GOLD",             "黃金 (GOLD)",                  "$", "down", "daily",     "GC=F",      "GOLDAMGBD228NLBM",  "黃金下降=市場越險需求低・風險偏好上升"),
  ("SILVER",           "白銀 (SILVER)",                "$", "down", "daily",     "SI=F",      "PSILVERUSDM",       "白銀下降=市場越險需求低"),
  ("WTI",              "原油 WTI (CL1!)",              "$", "down", "daily",     "CL=F",      "DCOILWTICO",        "油價下降=通脹壓力減少→有利市場"),
  ("BRENT",            "原油 Brent (BRENT)",           "$", "down", "daily",     "BZ=F",      "DCOILBRENTEU",      "Brent油價下降=通脹壓力減少→有利市場"),
  # 加密（CoinGecko，無需Key）
  ("BTC.D",            "比特幣市占率 (BTC.D)",         "%", "down", "live",      None,        None,                "BTC.D下降=資金流向山寨幣・風險偏好上升"),
  # 美元
  ("DXY",              "美元指數 DXY (DTWEXBGS)",      "", "down",  "daily",     "DX-Y.NYB",  "DTWEXBGS",          "美元下降=全球流動性改善・有利風險資產"),
  # 加密（CoinGecko）
  ("OTHERS.D",         "山寨幣市占率 (OTHERS.D)",      "%", "up",   "live",      None,        None,                "山寨幣市占率上升=Alt Season・風險偏好極度上升"),
  ("USDC.D",           "穩定幣市占率 USDC",            "%", "down", "live",      None,        None,                "穩定幣市占率下降=資金流入風險資產→看漲"),
  ("USDT.D",           "穩定幣市占率 USDT",            "%", "down", "live",      None,        None,                "穩定幣市占率下降=資金投入風險資產→看漲"),
  # 美國廣義利率
  ("USINTR",           "美國利率（廣義）USINTR",       "%", "down", "monthly",   None,        "FEDFUNDS",          "反映美國廣義利率環境"),
  # Fed 流動性
  ("WALCL",            "美聯儲資產負債表 (WALCL)",     "M$","up",   "weekly",    None,        "WALCL",             "擴表QE=放水→利好市場 | 縮表QT=收水→不利市場"),
  ("M2SL",             "美國 M2 貨幣供給量 (M2SL)",   "B$","up",   "monthly",   None,        "M2SL",              "M2成長=市場能用的錢增多→資產價格獲支撐"),
  ("TGA",              "財政部一般賬戶 TGA (WTREGEN)", "M$","down", "daily",     None,        "WTREGEN",           "TGA下降=政府花錢放回市場(bullish) | TGA上升=從市場回收資金(bearish)"),
  ("RRP_AMT",          "隔夜逆回購每日 (RRPONTSYD)",  "M$","down", "daily",     None,        "RRPONTSYD",         "RRP下降=市場把多餘資金投出去→更多錢流入風險資產"),
  ("RRP_TL",           "逆回購餘額 Total Level",       "M$","down", "daily",     None,        "RRPONTSYD",         "目前卡在Fed的資金總量 | 越低代表市場流動性越充裕"),
  ("RRP_RATE",         "逆回購中標利率 RRPONTSY AWARD","%","down",  "daily",     None,        "IORB",              "Fed給逆回購的利率 | 下降=停在Fed不划算・資金流回市場"),
  # 美國利率
  ("FEDFUNDS",         "聯邦基金有效利率 (FEDFUNDS)",  "%", "down", "monthly",   None,        "FEDFUNDS",          "降息=融資成本降低→有利股市 | 加息=壓制估值"),
  ("DGS10",            "美國10年債殖利率 (US10)",      "%", "down", "daily",     None,        "DGS10",             "長期資金成本+對通脹與經濟的預期 | 下降=折現率降低→估值空間打開"),
  ("DGS2",             "美國2年期公債殖利率 (DGS2)",   "%", "down", "daily",     None,        "DGS2",              "市場對未來1-2年利率政策走向的預期 | 下降=押注降息"),
  ("DFII10",           "10年公債實際殖利率 (GDGS10)",  "%", "down", "daily",     None,        "DFII10",            "長期資金成本（含通脹）| 下降=折現率降低→估值空間打開"),
  # 全球央行利率
  ("JPINTR",           "日本利率 JPINTR",             "%", "down", "monthly",   None,        "IRSTCI01JPM156N",   "反映日本利率環境 | 日本加息=Yen Carry Trade平倉風險"),
  ("CNINTR",           "中國利率 CNINTR",             "%", "down", "monthly",   None,        "INTDSRCNM193N",     "中國寬鬆=有助全球成長預期・支撐大宗商品需求"),
  ("DEINTR",           "德國利率 DEINTR",             "%", "down", "monthly",   None,        "IRSTCI01DEM156N",   "反映歐元區（德國代表）利率環境"),
  ("ININTR",           "印度利率 ININTR",             "%", "down", "monthly",   None,        "IRSTCI01INM156N",   "印度寬鬆=支持新興市場整體表現"),
  ("GBINTR",           "英國利率 GBINTR",             "%", "down", "monthly",   None,        "IRSTCI01GBM156N",   "英格蘭銀行利率環境"),
  ("FRINTR",           "法國利率 FRINTR",             "%", "down", "monthly",   None,        "IRSTCI01FRM156N",   "法國/ECB利率環境"),
  ("ITINTR",           "義大利利率 ININTR",           "%", "down", "monthly",   None,        "IRSTCI01ITM156N",   "義大利/ECB利率環境"),
  ("BRINTR",           "巴西利率 BRINTR",             "%", "down", "monthly",   None,        "IRSTCI01BRM156N",   "新興市場代表性高利率 | 降息有利新興市場資金流入"),
  ("CAINTR",           "加拿大利率 CAINTR",           "%", "down", "monthly",   None,        "IRSTCI01CAM156N",   "加拿大央行通常比Fed早行動，具前瞻意義"),
]

# ── 評分函數（與 JS 版本完全對應）────────────────────────
def sc_vix(v, h=None):
    if v<=13: return 9.5
    elif v<=16: return 8.5
    elif v<=19: return 7.0
    elif v<=22: return 5.5
    elif v<=26: return 4.0
    elif v<=30: return 2.5
    elif v<=40: return 1.5
    else: return 0.0

def sc_comm(v, h):  # 商品 YoY，下降=bullish
    if h is None or len(h) < 252: return 5.0
    p = float(h.iloc[-min(len(h),252)])
    if p == 0: return 5.0
    yoy = (v - p) / abs(p) * 100
    if yoy<-20: return 8.5
    elif yoy<-10: return 7.5
    elif yoy<0: return 6.5
    elif yoy<10: return 5.0
    elif yoy<20: return 3.5
    elif yoy<35: return 2.0
    else: return 1.0

def sc_dxy(v, h):
    if h is None or len(h) < 63: return 5.0
    p = float(h.iloc[-min(len(h),63)])
    if p == 0: return 5.0
    c = (v - p) / p * 100
    if c<-4: return 8.5
    elif c<-2: return 7.0
    elif c<0: return 6.0
    elif c<2: return 4.5
    elif c<4: return 3.0
    else: return 1.5

def sc_btcd(v):
    if v<40: return 8.5
    elif v<50: return 7.0
    elif v<55: return 5.5
    elif v<60: return 4.0
    elif v<65: return 2.5
    else: return 1.5

def sc_others(v): return 8.5 if v>25 else 7.0 if v>20 else 5.5 if v>15 else 4.0 if v>10 else 2.5
def sc_stable(v): return 9.0 if v<2 else 7.5 if v<4 else 6.0 if v<6 else 4.5 if v<8 else 3.0 if v<10 else 1.5
def sc_rrp(v):
    B = v / 1000
    return 9.5 if B<=50 else 8.0 if B<=150 else 6.5 if B<=300 else 5.0 if B<=600 else 3.0 if B<=1000 else 1.5 if B<=1500 else 0.5
def sc_rrp_rate(v):
    return 9.0 if v<=0.5 else 7.5 if v<=1.5 else 6.0 if v<=2.5 else 4.5 if v<=3.5 else 3.0 if v<=4.5 else 1.5 if v<=5.5 else 0.5
def sc_10y(v):
    return 9.0 if v<=1.5 else 8.0 if v<=2.0 else 7.0 if v<=2.5 else 5.5 if v<=3.5 else 3.5 if v<=4.5 else 2.0 if v<=5.5 else 0.5
def sc_2y(v):
    return 9.0 if v<=1.0 else 7.5 if v<=2.0 else 6.0 if v<=3.0 else 4.5 if v<=4.0 else 3.0 if v<=5.0 else 1.5
def sc_tips(v):
    return 9.5 if v<=-1.5 else 8.0 if v<=-0.5 else 6.5 if v<=0 else 5.0 if v<=0.5 else 3.5 if v<=1.0 else 2.0 if v<=1.5 else 0.5
def sc_glb(v):
    return 8.5 if v<=0.5 else 7.0 if v<=1.5 else 5.5 if v<=3.0 else 4.0 if v<=5.0 else 2.5 if v<=7.0 else 1.0

def sc_walcl(v, h):
    if h is None or len(h) < 26: return 5.0
    p = float(h.iloc[-min(len(h),26)])
    if p == 0: return 5.0
    c = (v - p) / abs(p) * 100
    return 9.0 if c>=3 else 7.5 if c>=1 else 5.5 if c>=-0.5 else 3.5 if c>=-2 else 2.0 if c>=-4 else 0.5

def sc_m2(v, h):
    if h is None or len(h) < 12: return 5.0
    p = float(h.iloc[-min(len(h),12)])
    if p == 0: return 5.0
    c = (v - p) / abs(p) * 100
    return 9.0 if c>=7 else 7.5 if c>=4 else 5.5 if c>=1 else 4.0 if c>=-1 else 2.5 if c>=-3 else 1.0

def sc_tga(v, h):
    B = v / 1000
    lv = 8.5 if B<=100 else 7.0 if B<=300 else 5.5 if B<=600 else 4.0 if B<=900 else 2.0
    tr = 5.0
    if h is not None and len(h) >= 12:
        pB = float(h.iloc[-min(len(h),12)]) / 1000
        d = B - pB
        tr = 8.5 if d<-200 else 7.0 if d<-50 else 6.0 if d<0 else 4.5 if d<100 else 3.0 if d<300 else 1.5
    return (lv + tr) / 2

def sc_ff(v, h):
    b = 9.0 if v<=0.5 else 7.5 if v<=1.5 else 6.0 if v<=2.5 else 4.5 if v<=3.5 else 3.0 if v<=4.5 else 1.5 if v<=5.5 else 0.5
    if h is not None and len(h) > 3:
        p = float(h.iloc[-min(len(h),3)])
        if v < p - 0.1: b = min(10, b + 1)
        elif v > p + 0.1: b = max(0, b - 1)
    return b

def compute_score(sid, v, h):
    """根據系列ID計算評分"""
    try:
        fmap = {
            "VIXCLS": sc_vix, "^VIX": sc_vix,
            "GOLD": sc_comm,  "GC=F": sc_comm,
            "SILVER": sc_comm,"SI=F": sc_comm,
            "WTI": sc_comm,   "CL=F": sc_comm,
            "BRENT": sc_comm, "BZ=F": sc_comm,
            "BTC.D": lambda v,h: sc_btcd(v),
            "DXY": sc_dxy, "DX-Y.NYB": sc_dxy,
            "OTHERS.D": lambda v,h: sc_others(v),
            "USDC.D": lambda v,h: sc_stable(v),
            "USDT.D": lambda v,h: sc_stable(v),
            "WALCL": sc_walcl,
            "M2SL": sc_m2,
            "TGA": sc_tga,  "WTREGEN": sc_tga,
            "RRP_AMT": lambda v,h: sc_rrp(v),
            "RRP_TL": lambda v,h: sc_rrp(v),
            "RRPONTSYD": lambda v,h: sc_rrp(v),
            "RRP_RATE": lambda v,h: sc_rrp_rate(v),
            "IORB": lambda v,h: sc_rrp_rate(v),
            "USINTR": sc_ff,
            "FEDFUNDS": sc_ff,
            "DGS10": lambda v,h: sc_10y(v),
            "DGS2": lambda v,h: sc_2y(v),
            "DFII10": lambda v,h: sc_tips(v),
        }
        fn = fmap.get(sid, lambda v,h: sc_glb(v))
        return round(float(fn(v, h)), 1)
    except:
        return 5.0

def build_hist(sid, series):
    """建立歷史評分序列 [[timestamp_ms, score], ...]"""
    if series is None or len(series) == 0:
        return []
    step = max(1, len(series) // MAX_HIST)
    pts = []
    for i in range(0, len(series), step):
        sub = series.iloc[:i+1]
        v = float(sub.iloc[-1])
        if np.isnan(v): continue
        sc = compute_score(sid, v, sub)
        ts = int(pd.Timestamp(sub.index[-1]).timestamp() * 1000)
        pts.append([ts, sc])
    return pts

def fmt_val(sid, v, unit):
    BIG_M = {"WALCL","TGA","WTREGEN","RRPONTSYD","RRP_AMT","RRP_TL"}
    BIG_B = {"M2SL"}
    if sid in BIG_M:
        B = v / 1000
        return f"{B/1000:.2f}T" if B >= 1000 else f"{B:.0f}B"
    if sid in BIG_B:
        return f"{v/1000:.1f}T"
    if unit == "$":
        return f"${v:,.2f}"
    if unit == "%":
        return f"{v:.2f}%"
    return f"{v:.2f}"

def get_trend(series, freq):
    lb = {"daily":20,"monthly":3,"weekly":8}.get(freq, 5)
    if series is None or len(series) <= lb: return "f"
    c = float(series.iloc[-1])
    p = float(series.iloc[-lb])
    if p == 0: return "f"
    r = (c - p) / abs(p)
    return "u" if r > 0.005 else "d" if r < -0.005 else "f"

# ── 數據抓取 ────────────────────────────────────────────
def fetch_yahoo(ticker_str):
    print(f"  [yfinance] {ticker_str} …", end=" ")
    try:
        t = yf.Ticker(ticker_str)
        h = t.history(start=START_DATE, auto_adjust=True)
        s = h["Close"].dropna()
        s.index = pd.DatetimeIndex([d.date() for d in s.index])
        print(f"OK ({len(s)} pts)")
        return s
    except Exception as e:
        print(f"FAIL: {e}")
        return None

def fetch_fred_series(fred, fred_id):
    print(f"  [FRED] {fred_id} …", end=" ")
    try:
        s = fred.get_series(fred_id, observation_start=START_DATE)
        s = s.dropna()
        print(f"OK ({len(s)} pts)")
        return s
    except Exception as e:
        print(f"FAIL: {e}")
        return None

def fetch_coingecko():
    print("  [CoinGecko] global …", end=" ")
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=15)
        r.raise_for_status()
        pct = r.json()["data"]["market_cap_percentage"]
        btcd=pct.get("btc",0); ethd=pct.get("eth",0)
        usdtd=pct.get("usdt",0); bnd=pct.get("bnb",0)
        usdcd=pct.get("usdc",0); xrpd=pct.get("xrp",0)
        others=max(0,100-btcd-ethd-usdtd-bnd-usdcd-xrpd)
        print("OK")
        return {"BTC.D":btcd,"OTHERS.D":others,"USDC.D":usdcd,"USDT.D":usdtd}
    except Exception as e:
        print(f"FAIL: {e}")
        return {}

def fetch_treasury_tga():
    print("  [Treasury FiscalData] TGA …", end=" ")
    try:
        params = {
            "fields": "record_date,account_type,open_today_bal",
            "filter": "account_type:eq:Federal Reserve Account",
            "sort": "-record_date",
            "page[size]": "730", "page[number]": "1"
        }
        r = requests.get(
            "https://api.fiscaldata.treasury.gov/services/api/v1/accounting/od/dts_table_1",
            params=params, timeout=20)
        r.raise_for_status()
        rows = r.json().get("data", [])
        if not rows: raise ValueError("No data")
        dates = pd.to_datetime([d["record_date"] for d in rows])
        vals = pd.to_numeric([d["open_today_bal"] for d in rows], errors="coerce")
        s = pd.Series(vals.values, index=dates).dropna().sort_index()
        print(f"OK ({len(s)} pts)")
        return s
    except Exception as e:
        print(f"FAIL: {e}")
        return None

def fetch_nyfed_rrp():
    print("  [NY Fed] RRP …", end=" ")
    urls = [
        "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/results/latest.json",
        "https://markets.newyorkfed.org/api/rp/all/results/lastTwoWeeks.json",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=15, headers={"Accept":"application/json"})
            if not r.ok: continue
            j = r.json()
            ops = (j.get("rp") or j.get("repo") or {}).get("operations") or j.get("operations",[])
            if not ops: continue
            latest = ops[0]
            det = latest.get("details") or (latest.get("propositions") or {}).get("details") or {}
            amt = (det.get("totalAmtAccepted") or det.get("totalAmtSubmitted") or 0) / 1e6
            rate = det.get("weightedAvgRate") or det.get("weightedAverageRate") or 0
            date = latest.get("operationDate") or latest.get("date") or datetime.today().strftime("%Y-%m-%d")
            print(f"OK (${amt:.0f}B, rate={rate}%)")
            return {"amt": amt, "rate": float(rate), "date": date}
        except: continue
    print("FAIL (will use FRED fallback)")
    return None

# ── 主流程 ────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"Macro Dashboard Data Fetcher")
    print(f"Start: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if not FRED_KEY:
        print("ERROR: FRED_API_KEY not set in environment"); sys.exit(1)
    fred = Fred(api_key=FRED_KEY)

    result = {
        "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "indicators": {}
    }

    # ── Step 1: CoinGecko（免費無需Key）──
    print("── CoinGecko ──")
    cg = fetch_coingecko()

    # ── Step 2: NY Fed RRP（免費無需Key）──
    print("── NY Fed ──")
    nyfed = fetch_nyfed_rrp()

    # ── Step 3: US Treasury TGA（免費無需Key）──
    print("── US Treasury FiscalData ──")
    tga_series = fetch_treasury_tga()

    # ── Step 4: 預先抓取共用 FRED 系列（避免重複請求）──
    print("\n── FRED (共用系列) ──")
    fred_cache = {}
    for sid, cn, unit, fav, freq, yf_ticker, fred_id, desc in INDICATORS:
        if fred_id and fred_id not in fred_cache and yf_ticker is None and sid not in cg:
            s = fetch_fred_series(fred, fred_id)
            if s is not None:
                fred_cache[fred_id] = s
            time.sleep(0.1)

    # ── Step 5: 組裝每個指標的數據 ──
    print("\n── 組裝指標 ──")
    for sid, cn, unit, fav, freq, yf_ticker, fred_id, desc in INDICATORS:
        entry = {"cn": cn, "unit": unit, "fav": fav, "freq": freq, "desc": desc,
                 "error": True, "v": None, "str": "N/A", "date": None,
                 "score": None, "trend": "f", "src": "unknown", "hist": []}
        try:
            series = None

            # CoinGecko 數據
            if sid in cg:
                v = cg[sid]
                entry.update({
                    "error": False, "v": v,
                    "str": f"{v:.1f}%",
                    "date": datetime.today().strftime("%Y-%m-%d"),
                    "score": compute_score(sid, v, None),
                    "trend": "f", "src": "coingecko", "hist": []
                })
                result["indicators"][sid] = entry
                continue

            # TGA – 優先用 Treasury，再 FRED
            if sid == "TGA":
                series = tga_series
                src = "treasury"
                if series is None:
                    series = fred_cache.get("WTREGEN")
                    src = "fred"

            # RRP – 優先用 NY Fed，再 FRED
            elif sid == "RRP_AMT" or sid == "RRP_TL":
                if nyfed:
                    v = nyfed["amt"]
                    ind = INDICATORS[[i[0] for i in INDICATORS].index(sid)]
                    # 用 FRED RRPONTSYD 建立歷史圖表
                    hist_series = fred_cache.get("RRPONTSYD")
                    hist = build_hist("RRPONTSYD", hist_series) if hist_series is not None else []
                    entry.update({
                        "error": False, "v": v,
                        "str": fmt_val(sid, v, unit),
                        "date": nyfed["date"],
                        "score": sc_rrp(v),
                        "trend": get_trend(hist_series, "daily") if hist_series is not None else "f",
                        "src": "nyfed", "hist": hist
                    })
                    result["indicators"][sid] = entry
                    continue
                else:
                    series = fred_cache.get("RRPONTSYD")
                    src = "fred"

            elif sid == "RRP_RATE":
                series = fred_cache.get("IORB")
                src = "fred"
                if series is None:
                    # Try fetching IORB
                    series = fetch_fred_series(fred, "IORB")
                    if series is not None: fred_cache["IORB"] = series

            # yfinance 優先（VIX/黃金/白銀/原油/DXY）
            elif yf_ticker:
                series = fetch_yahoo(yf_ticker)
                src = "yahoo"
                if series is None and fred_id:  # 回退 FRED
                    series = fred_cache.get(fred_id)
                    if series is None:
                        series = fetch_fred_series(fred, fred_id)
                        if series is not None: fred_cache[fred_id] = series
                    src = "fred"

            # 純 FRED
            elif fred_id:
                series = fred_cache.get(fred_id)
                src = "fred"
                if series is None:
                    series = fetch_fred_series(fred, fred_id)
                    if series is not None: fred_cache[fred_id] = series

            if series is None or len(series) == 0:
                result["indicators"][sid] = entry
                continue

            v = float(series.iloc[-1])
            if np.isnan(v):
                result["indicators"][sid] = entry
                continue

            score = compute_score(sid, v, series)
            trend = get_trend(series, freq)
            hist = build_hist(sid, series)

            entry.update({
                "error": False, "v": v,
                "str": fmt_val(sid, v, unit),
                "date": str(series.index[-1])[:10],
                "score": score,
                "trend": trend,
                "src": src,
                "hist": hist
            })
            print(f"  ✓ {sid}: {entry['str']} → score={score}")

        except Exception as e:
            print(f"  ✗ {sid}: {e}")
            traceback.print_exc()

        result["indicators"][sid] = entry
        time.sleep(0.05)

    # ── 計算綜合評分 ──
    scores = [v["score"] for v in result["indicators"].values()
              if not v.get("error") and v.get("score") is not None]
    result["overall_score"] = round(sum(scores)/len(scores), 1) if scores else None
    result["scored_count"] = len(scores)

    # ── 寫入 data.json ──
    out_path = os.path.join(os.path.dirname(__file__), "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, separators=(",", ":"), ensure_ascii=False, default=str)

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\n{'='*60}")
    print(f"✅ data.json 寫入完成 ({size_kb:.1f} KB)")
    print(f"   {len(scores)}/{len(INDICATORS)} 指標成功")
    print(f"   綜合評分: {result['overall_score']}")
    print(f"   完成: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
