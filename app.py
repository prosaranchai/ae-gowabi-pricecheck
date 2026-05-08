import streamlit as st
import pandas as pd
import anthropic
import json
import re
import time
import io

st.set_page_config(page_title="Price Strategy Finder", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0A0A12; color: #F0EAFF; }
    .block-container { padding-top: 1.5rem; }
    div[data-testid="metric-container"] {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px; padding: 10px 16px;
    }
    .cheaper { color: #34D399; font-weight: bold; }
    .expensive { color: #F87171; }
    .same { color: #FBBF24; }
</style>
""", unsafe_allow_html=True)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ ตั้งค่า")
    api_key = st.text_input("🔑 Anthropic API Key", type="password", placeholder="sk-ant-...")
    if api_key:
        st.success("✅ API Key พร้อมใช้งาน")
    else:
        st.warning("กรุณาใส่ API Key")
        st.markdown("[ขอ API Key →](https://console.anthropic.com/)")

    st.markdown("---")
    st.markdown("## 📱 Platform เป้าหมาย")
    st.caption("(Gowabi อ่านจากไฟล์โดยตรง)")
    search_hdmall  = st.checkbox("🏥 HD Mall",     value=True)
    search_klook   = st.checkbox("🎫 Klook",       value=True)
    search_inhouse = st.checkbox("🏪 เว็บร้านเอง", value=True)

    st.markdown("---")
    st.markdown("## 🐛 Debug Mode")
    debug_mode = st.checkbox("แสดง raw response จาก AI (สำหรับ troubleshoot)", value=False)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fmt(n):
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return "—"
    try:
        return f"฿{int(float(n)):,}"
    except:
        return "—"

def compare_badge(gowabi_low, competitor_price):
    """Return emoji+label based on price comparison."""
    if competitor_price is None:
        return ""
    try:
        g = float(gowabi_low)
        c = float(competitor_price)
        if c < g:
            diff = round(((g - c) / g) * 100)
            return f"🟢 ถูกกว่า {diff}%"
        elif c > g:
            diff = round(((c - g) / g) * 100)
            return f"🔴 แพงกว่า {diff}%"
        else:
            return "🟡 เท่ากัน"
    except:
        return ""

def extract_core_keyword(service_name: str) -> str:
    """ตัดชื่อ Gowabi ที่ยาวออกเหลือแค่ core service keyword."""
    name = str(service_name)
    name = re.sub(r'\s*[-–]\s*(Free|Performed|Authentic|Unboxed|Doctor|Senior|Professor|Buy|100%|Please).*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\((?:Performed|Authentic|Unboxed|Senior|Professor|Buy \d|100%|Please|Free|Doctor)[^)]*\)', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\[.*?\]', '', name)
    name = re.sub(r'\s*\*+.*', '', name)
    name = re.sub(r'".*?"', '', name)
    name = re.sub(r'\s{2,}', ' ', name).strip()
    return name[:80]


import requests
from bs4 import BeautifulSoup
import urllib.parse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def extract_price_from_text(text: str) -> int | None:
    """Extract first numeric price (฿ or บาท pattern) from text."""
    text = text.replace(",", "")
    patterns = [
        r'฿\s*(\d{2,6})',
        r'(\d{2,6})\s*บาท',
        r'(\d{2,6})\s*THB',
        r'price["\s:]+(\d{2,6})',
        r'"price"\s*:\s*(\d{2,6})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 50 <= val <= 999999:   # sanity check
                return val
    return None

def search_hdmall(keyword: str, debug: bool = False) -> dict:
    try:
        q = urllib.parse.quote(keyword)
        url = f"https://hdmall.co.th/search?q={q}"
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        if debug:
            st.caption(f"HD Mall status: {r.status_code} | url: {url}")

        # Find price elements — HD Mall uses data-price or .price class
        prices = []
        items = []

        # Try JSON-LD first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                j = json.loads(script.string or "")
                if isinstance(j, list): j = j[0]
                if j.get("@type") in ("Product", "Offer"):
                    p = j.get("offers", {}).get("price") or j.get("price")
                    if p: prices.append(int(float(p)))
                    items.append(j.get("name", ""))
            except: pass

        # Try common price selectors
        for sel in [".price", ".product-price", "[data-price]", ".selling-price", ".final-price", "span.price"]:
            for el in soup.select(sel)[:10]:
                txt = el.get_text(strip=True)
                p = extract_price_from_text(txt)
                if p: prices.append(p)

        # Try product titles
        for sel in [".product-name", ".product-title", "h3.title", ".item-name"]:
            for el in soup.select(sel)[:3]:
                items.append(el.get_text(strip=True)[:60])

        if debug:
            st.caption(f"HD Mall prices found: {prices[:5]} | items: {items[:3]}")

        if prices:
            return {
                "found": True,
                "minPrice": min(prices),
                "maxPrice": max(prices) if len(prices) > 1 else min(prices),
                "discount": None,
                "topItem": items[0][:60] if items else keyword,
                "url": url,
                "note": f"พบ {len(prices)} รายการ"
            }
        return {"found": False, "minPrice": None, "maxPrice": None, "discount": None,
                "topItem": None, "url": url, "note": "ไม่พบราคาใน HTML"}
    except Exception as e:
        return {"found": False, "minPrice": None, "maxPrice": None, "discount": None,
                "topItem": None, "url": None, "note": f"error: {str(e)[:50]}"}


def search_klook(keyword: str, debug: bool = False) -> dict:
    try:
        q = urllib.parse.quote(keyword)
        url = f"https://www.klook.com/en-GB/search/?query={q}&countryCode=TH"
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        if debug:
            st.caption(f"Klook status: {r.status_code} | url: {url}")

        prices = []
        items = []

        # Klook embeds data in __NEXT_DATA__ script
        next_data = soup.find("script", id="__NEXT_DATA__")
        if next_data:
            try:
                data = json.loads(next_data.string or "")
                # Walk the JSON tree looking for price fields
                def find_prices(obj, depth=0):
                    if depth > 10: return
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("price", "fromPrice", "originalPrice", "discountPrice") and isinstance(v, (int, float)):
                                if 50 <= v <= 999999: prices.append(int(v))
                            elif k in ("title", "name", "activityName") and isinstance(v, str):
                                items.append(v[:60])
                            else:
                                find_prices(v, depth+1)
                    elif isinstance(obj, list):
                        for item in obj[:20]:
                            find_prices(item, depth+1)
                find_prices(data)
            except: pass

        # Fallback: text selectors
        for sel in [".price", ".from-price", "[class*='price']", "[class*='Price']"]:
            for el in soup.select(sel)[:10]:
                p = extract_price_from_text(el.get_text())
                if p: prices.append(p)

        if debug:
            st.caption(f"Klook prices found: {prices[:5]} | items: {items[:3]}")

        if prices:
            return {
                "found": True,
                "minPrice": min(prices),
                "maxPrice": max(prices) if len(prices) > 1 else min(prices),
                "discount": None,
                "topItem": items[0][:60] if items else keyword,
                "url": url,
                "note": f"พบ {len(prices)} รายการ"
            }
        return {"found": False, "minPrice": None, "maxPrice": None, "discount": None,
                "topItem": None, "url": url, "note": "ไม่พบราคาใน HTML (อาจ JS-rendered)"}
    except Exception as e:
        return {"found": False, "minPrice": None, "maxPrice": None, "discount": None,
                "topItem": None, "url": None, "note": f"error: {str(e)[:50]}"}


def search_inhouse(keyword: str, shop_name: str, debug: bool = False) -> dict:
    """Search shop's own website via DuckDuckGo."""
    try:
        clean_shop = re.sub(r'\(.*?\)', '', str(shop_name)).strip()
        q = urllib.parse.quote(f"{clean_shop} {keyword} ราคา")
        url = f"https://html.duckduckgo.com/html/?q={q}"
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        if debug:
            st.caption(f"DuckDuckGo status: {r.status_code} | query: {clean_shop} {keyword} ราคา")

        results = soup.select(".result__body")[:5]
        prices = []
        best_url = None
        best_title = None

        for res in results:
            text = res.get_text()
            # Skip Gowabi/HD Mall/Klook results — want own site
            if any(x in text.lower() for x in ["gowabi", "hdmall", "klook"]):
                continue
            p = extract_price_from_text(text)
            if p:
                prices.append(p)
                link = res.select_one("a.result__url")
                if link and not best_url:
                    best_url = link.get("href", "")
                title = res.select_one(".result__title")
                if title and not best_title:
                    best_title = title.get_text(strip=True)[:60]

        if debug:
            st.caption(f"In-house prices: {prices[:5]}")

        if prices:
            return {
                "found": True,
                "minPrice": min(prices),
                "maxPrice": max(prices) if len(prices) > 1 else min(prices),
                "discount": None,
                "topItem": best_title or keyword,
                "url": best_url,
                "note": "จาก DuckDuckGo search"
            }
        return {"found": False, "minPrice": None, "maxPrice": None, "discount": None,
                "topItem": None, "url": None, "note": "ไม่พบราคาบนเว็บร้าน"}
    except Exception as e:
        return {"found": False, "minPrice": None, "maxPrice": None, "discount": None,
                "topItem": None, "url": None, "note": f"error: {str(e)[:50]}"}


def search_competitor_prices(api_key, service_name, shop_name, platforms, debug=False):
    core_name = extract_core_keyword(service_name)
    results = []
    for p in platforms:
        if p == "HD Mall":
            r = search_hdmall(core_name, debug=debug)
        elif p == "Klook":
            r = search_klook(core_name, debug=debug)
        else:
            r = search_inhouse(core_name, shop_name, debug=debug)
        r["platform"] = p
        results.append(r)
        time.sleep(1)
    return {"search_keyword": core_name, "results": results}


def load_file(uploaded):
    if uploaded.name.endswith(".csv"):
        df = pd.read_csv(uploaded)
    else:
        df = pd.read_excel(uploaded)

    # Normalize column names
    df.columns = [str(c).strip() for c in df.columns]

    # Map columns from Price_Strategy.xlsx format
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if "service_name" in cl or cl == "service name":
            col_map["service_name"] = c
        elif "chain" in cl or "shop" in cl:
            col_map["shop_name"] = c
        elif "lowest" in cl:
            col_map["gowabi_lowest"] = c
        elif "normal" in cl:
            col_map["gowabi_normal"] = c
        elif "duration" in cl:
            col_map["duration"] = c
        elif "service id" in cl or cl == "service id":
            col_map["service_id"] = c

    # Build clean dataframe
    out = pd.DataFrame()
    out["service_id"]     = df[col_map["service_id"]]     if "service_id"     in col_map else range(len(df))
    out["service_name"]   = df[col_map["service_name"]]   if "service_name"   in col_map else df.iloc[:, 0]
    out["shop_name"]      = df[col_map["shop_name"]]      if "shop_name"      in col_map else ""
    out["duration"]       = df[col_map["duration"]]       if "duration"       in col_map else ""
    out["gowabi_normal"]  = pd.to_numeric(df[col_map["gowabi_normal"]],  errors="coerce") if "gowabi_normal"  in col_map else None
    out["gowabi_lowest"]  = pd.to_numeric(df[col_map["gowabi_lowest"]],  errors="coerce") if "gowabi_lowest"  in col_map else None

    # Drop rows with no service name or #N/A
    out = out[out["service_name"].notna()]
    out = out[~out["service_name"].astype(str).str.startswith("#")]
    out = out[out["service_name"].astype(str).str.strip() != ""]
    out = out.reset_index(drop=True)
    return out


# ─── Main ──────────────────────────────────────────────────────────────────────
st.markdown("# 📊 Price Strategy Finder")
st.markdown("อ่านราคา **Gowabi** จากไฟล์ → ค้นหา **HD Mall · Klook · เว็บร้านเอง** แล้วเปรียบเทียบอัตโนมัติ")
st.divider()

# ── File Upload ──
col_up, col_tmpl = st.columns([3, 1])
with col_tmpl:
    template_rows = "Service ID,service_name,Duration,Chain / Shops name,Gowabi Normal Price,Gowabi Lowest Price\n354517,Botox Nabota 50 unit,30,Dr.J Clinic,2999,1999\n75615,Thai Massage 120 min,120,Cha Spa,599,599\n"
    st.download_button("⬇️ Template CSV", data=template_rows, file_name="template.csv", mime="text/csv")

uploaded = st.file_uploader("📂 อัปโหลดไฟล์ Price_Strategy (.xlsx หรือ .csv)", type=["csv","xlsx","xls"])

if not uploaded:
    st.info("📂 รองรับ format เดียวกับ Price_Strategy.xlsx — มีคอลัมน์ `service_name`, `Chain/Shops name`, `Gowabi Normal Price`, `Gowabi Lowest Price`")
    st.stop()

# ── Parse ──
try:
    df = load_file(uploaded)
    st.success(f"✅ โหลดสำเร็จ **{len(df):,} รายการ** | ร้านค้า: {df['shop_name'].nunique()} ร้าน")
except Exception as e:
    st.error(f"❌ อ่านไฟล์ไม่ได้: {e}")
    st.stop()

# ── Summary metrics ──
c1, c2, c3, c4 = st.columns(4)
c1.metric("รายการทั้งหมด", f"{len(df):,}")
c2.metric("ร้านค้า", f"{df['shop_name'].nunique():,}")
valid_prices = df["gowabi_lowest"].dropna()
c3.metric("ราคา Gowabi เฉลี่ย", fmt(valid_prices.mean()) if len(valid_prices) else "—")
c4.metric("ช่วงราคา", f"{fmt(valid_prices.min())} – {fmt(valid_prices.max())}" if len(valid_prices) else "—")

st.divider()

# ── Filter ──
with st.expander("🔍 กรองรายการ (ไม่บังคับ)", expanded=False):
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        keyword_filter = st.text_input("ค้นหาชื่อบริการ")
    with col_f2:
        shops = ["ทั้งหมด"] + sorted(df["shop_name"].dropna().unique().tolist())
        shop_filter = st.selectbox("ร้านค้า", shops)
    with col_f3:
        max_rows = st.number_input("จำนวนสูงสุดที่จะค้นหา", min_value=1, max_value=len(df), value=min(20, len(df)))

df_filtered = df.copy()
if keyword_filter:
    df_filtered = df_filtered[df_filtered["service_name"].str.contains(keyword_filter, case=False, na=False)]
if shop_filter != "ทั้งหมด":
    df_filtered = df_filtered[df_filtered["shop_name"] == shop_filter]
df_filtered = df_filtered.head(int(max_rows)).reset_index(drop=True)

st.caption(f"📋 จะค้นหา **{len(df_filtered):,}** รายการ")

# ── Platform selection check ──
active_platforms = []
if search_hdmall:  active_platforms.append("HD Mall")
if search_klook:   active_platforms.append("Klook")
if search_inhouse: active_platforms.append("เว็บร้านเอง")

if not api_key:
    st.error("⚠️ กรุณาใส่ Anthropic API Key ในแถบซ้ายมือ")
    st.stop()
if not active_platforms:
    st.warning("⚠️ กรุณาเลือก Platform อย่างน้อย 1 อัน")
    st.stop()

# ── Run ──
if st.button("🔍 เริ่มค้นหาราคา", type="primary", use_container_width=True):
    # Time estimate: ~15-20 sec per row (3 platforms × ~5s each + delays)
    est_min = len(df_filtered) * 15 // 60
    est_max = len(df_filtered) * 25 // 60
    est_sec = len(df_filtered) * 20
    if est_sec < 60:
        st.info(f"⏱️ ประมาณเวลา: **{est_sec} วินาที** ({len(df_filtered)} รายการ × ~20 วิ/รายการ)")
    else:
        st.info(f"⏱️ ประมาณเวลา: **{est_min}–{est_max} นาที** ({len(df_filtered)} รายการ × ~20 วิ/รายการ) — ช้าแต่ได้ผลลัพธ์จริงครับ")
    total = len(df_filtered)
    results_store = [None] * total

    progress_bar = st.progress(0)
    status_text  = st.empty()
    table_ph     = st.empty()
    done_rows    = []

    for i, row in df_filtered.iterrows():
        status_text.markdown(f"⏳ **{i+1}/{total}** — กำลังค้นหา: *{row['service_name'][:60]}...*")
        try:
            data = search_competitor_prices(api_key, row["service_name"], row["shop_name"], active_platforms, debug=debug_mode)
            results_store[i] = {"ok": True, "data": data}
        except Exception as e:
            results_store[i] = {"ok": False, "error": str(e)}

        # Build display row
        core_kw = extract_core_keyword(row["service_name"])
        r_display = {
            "#": i + 1,
            "Service ID": str(row.get("service_id", "")).replace(".0", ""),
            "บริการ (Gowabi)": str(row["service_name"])[:55] + ("…" if len(str(row["service_name"])) > 55 else ""),
            "🔍 keyword ที่ค้นหา": core_kw[:45],
            "ร้าน": str(row.get("shop_name", ""))[:30],
            "นาที": str(row.get("duration", "")).replace(".0", ""),
            "Gowabi ปกติ": fmt(row.get("gowabi_normal")),
            "Gowabi ต่ำสุด": fmt(row.get("gowabi_lowest")),
        }

        if results_store[i]["ok"]:
            d = results_store[i]["data"]
            cheapest_price = None
            cheapest_name  = None

            gowabi_low = row.get("gowabi_lowest")
            if pd.notna(gowabi_low):
                cheapest_price = float(gowabi_low)
                cheapest_name  = "Gowabi"

            for pname in active_platforms:
                pr = next((x for x in d.get("results", []) if x["platform"] == pname), None)
                if pr and pr.get("found") and pr.get("minPrice") is not None:
                    p_val = pr["minPrice"]
                    badge = compare_badge(gowabi_low, p_val) if pd.notna(gowabi_low) else ""
                    disc  = f" (-{pr['discount']}%)" if pr.get("discount") else ""
                    r_display[pname] = fmt(p_val) + disc + (" " + badge if badge else "")
                    if cheapest_price is None or p_val < cheapest_price:
                        cheapest_price = p_val
                        cheapest_name  = pname
                else:
                    r_display[pname] = "—"

            r_display["🏆 ถูกสุด"] = cheapest_name or "—"
        else:
            for pname in active_platforms:
                r_display[pname] = "❌"
            r_display["🏆 ถูกสุด"] = "—"

        done_rows.append(r_display)
        table_ph.dataframe(pd.DataFrame(done_rows), use_container_width=True, hide_index=True)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.2)

    status_text.success(f"✅ ค้นหาเสร็จทั้งหมด {total} รายการ!")

    # ── Export ──
    export_rows = []
    for i, row in df_filtered.iterrows():
        er = {
            "service_id":    row.get("service_id", ""),
            "service_name":  row["service_name"],
            "shop_name":     row.get("shop_name", ""),
            "duration":      row.get("duration", ""),
            "gowabi_normal": row.get("gowabi_normal"),
            "gowabi_lowest": row.get("gowabi_lowest"),
        }
        if results_store[i] and results_store[i]["ok"]:
            d = results_store[i]["data"]
            for pname in active_platforms:
                pr = next((x for x in d.get("results", []) if x["platform"] == pname), None)
                key = pname.replace(" ", "_").replace("เว็บร้านเอง", "inhouse")
                if pr and pr.get("found"):
                    er[f"{key}_minPrice"]  = pr.get("minPrice")
                    er[f"{key}_maxPrice"]  = pr.get("maxPrice")
                    er[f"{key}_discount%"] = pr.get("discount")
                    er[f"{key}_item"]      = pr.get("topItem")
                    er[f"{key}_url"]       = pr.get("url")
                    er[f"{key}_note"]      = pr.get("note")
                else:
                    er[f"{key}_minPrice"] = None
        export_rows.append(er)

    df_export = pd.DataFrame(export_rows)

    # Add cheapest platform column
    def get_cheapest(r):
        prices = {"Gowabi": r.get("gowabi_lowest")}
        for pname in active_platforms:
            key = pname.replace(" ", "_").replace("เว็บร้านเอง", "inhouse")
            prices[pname] = r.get(f"{key}_minPrice")
        valid = {k: v for k, v in prices.items() if v is not None and not (isinstance(v, float) and pd.isna(v))}
        return min(valid, key=valid.get) if valid else ""
    df_export["cheapest_platform"] = df_export.apply(get_cheapest, axis=1)

    csv_bytes = df_export.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label=f"⬇️ Export ผลลัพธ์ CSV ({len(df_filtered)} รายการ)",
        data=csv_bytes,
        file_name="price_comparison_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # ── Summary stats ──
    st.divider()
    st.markdown("### 📈 สรุปภาพรวม")
    cheapest_counts = df_export["cheapest_platform"].value_counts()
    cols = st.columns(len(cheapest_counts))
    for i, (platform, count) in enumerate(cheapest_counts.items()):
        pct = round(count / len(df_filtered) * 100)
        cols[i].metric(f"🏆 {platform}", f"{count} รายการ", f"{pct}% ของทั้งหมด")
