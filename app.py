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
    st.markdown("## ⚡ Concurrency")
    concurrency = st.slider("ค้นหาพร้อมกัน (rows)", 1, 3, 1)
    st.caption("แนะนำ 1-2 เพื่อลด rate limit")

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
    """ตัดชื่อ Gowabi ที่ยาวออกเหลือแค่ core service keyword สำหรับ search."""
    name = str(service_name)
    # ตัดทุกอย่างหลังวงเล็บแรก หรือ dash ที่มีคำว่า Free/Performed/Authentic
    name = re.sub(r'\s*[-–]\s*(Free|Performed|Authentic|Unboxed|Doctor|Senior|Professor|Buy|100%|Please).*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\((?:Performed|Authentic|Unboxed|Senior|Professor|Buy \d|100%|Please|Free|Doctor)[^)]*\)', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\[.*?\]', '', name)           # ตัด [Flash Sale eVoucher] ฯลฯ
    name = re.sub(r'\s*\*+.*', '', name)              # ตัด * หมายเหตุ
    name = re.sub(r'".*?"', '', name)                 # ตัดข้อความใน quote
    name = re.sub(r'\s{2,}', ' ', name).strip()
    return name[:80]  # จำกัดความยาว


def search_competitor_prices(api_key, service_name, shop_name, platforms):
    client = anthropic.Anthropic(api_key=api_key)
    platform_names = ", ".join(platforms)

    # ย่อชื่อบริการให้กระชับก่อน search
    core_name = extract_core_keyword(service_name)
    shop_ctx = f'ร้าน/คลินิก: "{shop_name}"' if shop_name and str(shop_name).strip() not in ["", "nan"] else "ไม่ระบุร้าน"

    search_queries = []
    for p in platforms:
        if p == "HD Mall":
            search_queries.append(f'site:hdmall.co.th "{core_name}"')
            search_queries.append(f'hdmall.co.th {core_name} ราคา')
        elif p == "Klook":
            search_queries.append(f'site:klook.com {core_name} Thailand')
            search_queries.append(f'klook Thailand {core_name}')
        elif p == "เว็บร้านเอง" and shop_name and str(shop_name).strip() not in ["", "nan"]:
            clean_shop = re.sub(r'\(.*?\)', '', str(shop_name)).strip()
            search_queries.append(f'{clean_shop} {core_name} ราคา')

    prompt = f"""ค้นหาราคาบริการ "{core_name}" (ชื่อเต็ม: "{service_name[:80]}")
จาก {shop_ctx} บน platform: {platform_names}

วิธีค้นหา — ค้นแยกทีละ platform:
{"- HD Mall: ค้น hdmall.co.th ด้วยคำ: " + core_name if "HD Mall" in platforms else ""}
{"- Klook: ค้น klook.com (Thailand/Bangkok/wellness/spa) ด้วยคำ: " + core_name if "Klook" in platforms else ""}
{"- เว็บร้านเอง: Google ชื่อร้าน + บริการ เช่น: " + re.sub(r'\\(.*?\\)', '', str(shop_name)).strip() + " " + core_name if "เว็บร้านเอง" in platforms else ""}

กฎสำคัญ:
1. ถ้าเจอบริการใกล้เคียง (ไม่จำเป็นต้องตรงทั้งหมด) ให้ found = true และระบุ topItem
2. ถ้า platform นั้นไม่มีบริการประเภทนี้เลย ให้ found = false
3. ค้นให้ได้ราคาจริง อย่าเดา

ตอบเป็น JSON เท่านั้น:
{{
  "search_keyword": "{core_name}",
  "results": [
    {{
      "platform": "ชื่อ platform",
      "found": true หรือ false,
      "minPrice": ตัวเลขหรือ null,
      "maxPrice": ตัวเลขหรือ null,
      "discount": เปอร์เซ็นต์ส่วนลดหรือ null,
      "topItem": "ชื่อแพ็คเกจที่เจอ หรือ null",
      "url": "url หรือ null",
      "note": "หมายเหตุ เช่น ไม่มีบริการนี้บน platform นี้เลย"
    }}
  ]
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("ไม่พบ JSON")
    return json.loads(m.group())


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
    total = len(df_filtered)
    results_store = [None] * total

    progress_bar = st.progress(0)
    status_text  = st.empty()
    table_ph     = st.empty()
    done_rows    = []

    for i, row in df_filtered.iterrows():
        status_text.markdown(f"⏳ **{i+1}/{total}** — กำลังค้นหา: *{row['service_name'][:60]}...*")
        try:
            data = search_competitor_prices(api_key, row["service_name"], row["shop_name"], active_platforms)
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
