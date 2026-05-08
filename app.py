import streamlit as st
import pandas as pd
import anthropic
import json
import re
import time
import io

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Mass Price Finder",
    page_icon="📊",
    layout="wide",
)

# ─── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0A0A12; }
    .stApp { background-color: #0A0A12; color: #F0EAFF; }
    .block-container { padding-top: 2rem; }
    .platform-badge {
        display: inline-block; padding: 3px 12px;
        border-radius: 100px; font-size: 0.78rem; font-weight: 700; margin: 2px;
    }
    .cheapest { color: #34D399; font-weight: 700; }
    .price { font-weight: 700; font-size: 1rem; }
    .discount { color: #86EFAC; font-size: 0.78rem; }
</style>
""", unsafe_allow_html=True)

# ─── Constants ─────────────────────────────────────────────────────────────────
PLATFORMS = {
    "Gowabi":      {"emoji": "💆", "color": "#FF6B9D"},
    "HD Mall":     {"emoji": "🏥", "color": "#00B4D8"},
    "Klook":       {"emoji": "🎫", "color": "#FF5722"},
    "เว็บร้านเอง": {"emoji": "🏪", "color": "#A78BFA"},
}

TEMPLATE_CSV = """keyword,shop
Botox,Nirunda Clinic
ฟอกสีฟัน,Dental Signature
Laser CO2,
นวดหน้า,Let Me Skin
"""

# ─── Helper: call Anthropic API ────────────────────────────────────────────────
def search_prices(api_key: str, keyword: str, shop: str, platforms: list[str]) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    platform_names = ", ".join(platforms)
    shop_ctx = f'ร้าน/คลินิก: "{shop}"' if shop.strip() else "ค้นหาราคาทั่วไป"

    prompt = f"""ค้นหาราคาบริการ "{keyword}" จาก {shop_ctx} บน platform: {platform_names}

ตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น:
{{
  "results": [
    {{
      "platform": "ชื่อ platform",
      "found": true หรือ false,
      "minPrice": ตัวเลขหรือ null,
      "maxPrice": ตัวเลขหรือ null,
      "originalPrice": ตัวเลขหรือ null,
      "discount": ตัวเลขหรือ null,
      "topItem": "ชื่อแพ็คเกจ หรือ null",
      "url": "url หรือ null",
      "note": "หมายเหตุสั้นๆ"
    }}
  ],
  "cheapest": "platform ที่ถูกที่สุด หรือ null"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("ไม่พบ JSON ในผลลัพธ์")
    return json.loads(m.group())


def fmt_price(n):
    if n is None:
        return "—"
    return f"฿{int(n):,}"


def price_range(r):
    if not r.get("found"):
        return "—"
    lo, hi = r.get("minPrice"), r.get("maxPrice")
    if lo is None:
        return "—"
    if hi and hi != lo:
        return f"{fmt_price(lo)} – {fmt_price(hi)}"
    return fmt_price(lo)


# ─── Sidebar: API Key ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ ตั้งค่า")
    api_key = st.text_input("🔑 Anthropic API Key", type="password",
                            placeholder="sk-ant-...")
    if api_key:
        st.success("✅ ใส่ API Key แล้ว")
    else:
        st.warning("กรุณาใส่ API Key ก่อนใช้งาน")
        st.markdown("[ขอ API Key ได้ที่นี่ →](https://console.anthropic.com/)")

    st.markdown("---")
    st.markdown("## 📱 เลือก Platform")
    selected = {}
    for name, meta in PLATFORMS.items():
        selected[name] = st.checkbox(f"{meta['emoji']} {name}", value=True)
    active_platforms = [k for k, v in selected.items() if v]

    st.markdown("---")
    st.markdown("## ⚡ Concurrency")
    concurrency = st.slider("ค้นหาพร้อมกัน (รายการ)", 1, 5, 2)

# ─── Main ──────────────────────────────────────────────────────────────────────
st.markdown("# 📊 Mass Price Finder")
st.markdown("อัปโหลด CSV / Excel แล้ว AI ค้นหาราคาให้ทุกรายการ — Gowabi · HD Mall · Klook · เว็บร้านเอง")
st.markdown("---")

# Template download
col1, col2 = st.columns([3, 1])
with col2:
    st.download_button(
        "⬇️ ดาวน์โหลด Template CSV",
        data=TEMPLATE_CSV,
        file_name="template.csv",
        mime="text/csv",
    )

# File upload
uploaded = st.file_uploader(
    "📂 อัปโหลดไฟล์รายการบริการ",
    type=["csv", "xlsx", "xls"],
    help="คอลัมน์ที่ 1: keyword (บริการ), คอลัมน์ที่ 2: shop (ชื่อร้าน, ไม่บังคับ)"
)

if uploaded:
    # ── Parse file ──
    try:
        if uploaded.name.endswith(".csv"):
            df_raw = pd.read_csv(uploaded)
        else:
            df_raw = pd.read_excel(uploaded)

        # Normalize columns
        df_raw.columns = [c.lower().strip() for c in df_raw.columns]
        kw_col = next((c for c in df_raw.columns if "keyword" in c or "service" in c or "บริการ" in c), df_raw.columns[0])
        sh_col = next((c for c in df_raw.columns if "shop" in c or "ร้าน" in c or "clinic" in c), None)

        df = pd.DataFrame()
        df["keyword"] = df_raw[kw_col].astype(str).str.strip()
        df["shop"] = df_raw[sh_col].fillna("").astype(str).str.strip() if sh_col else ""
        df = df[df["keyword"].notna() & (df["keyword"] != "") & (df["keyword"] != "nan")]
        df = df.reset_index(drop=True)

        st.success(f"✅ โหลดสำเร็จ {len(df)} รายการ")
    except Exception as e:
        st.error(f"❌ อ่านไฟล์ไม่ได้: {e}")
        st.stop()

    # ── Preview ──
    with st.expander("🔍 ดูรายการ", expanded=False):
        st.dataframe(df, use_container_width=True)

    if not api_key:
        st.error("⚠️ กรุณาใส่ Anthropic API Key ในแถบซ้ายมือก่อน")
        st.stop()

    if not active_platforms:
        st.warning("⚠️ กรุณาเลือก Platform อย่างน้อย 1 อัน")
        st.stop()

    # ── Run button ──
    if st.button("🔍 เริ่มค้นหาราคาทั้งหมด", type="primary", use_container_width=True):

        total = len(df)
        results_store = [None] * total

        progress_bar = st.progress(0, text="เริ่มต้น...")
        status_text = st.empty()
        result_container = st.container()

        # Build result rows incrementally
        cols_display = ["#", "keyword", "shop"] + active_platforms + ["🏆 ถูกสุด"]
        table_placeholder = result_container.empty()

        rows_done = []

        for i, row in df.iterrows():
            status_text.markdown(f"⏳ กำลังค้นหา **{row['keyword']}** ({i+1}/{total})...")
            try:
                data = search_prices(api_key, row["keyword"], row.get("shop", ""), active_platforms)
                results_store[i] = {"status": "done", "data": data}
            except Exception as e:
                results_store[i] = {"status": "error", "error": str(e)}

            # Build display row
            display_row = {"#": i + 1, "keyword": row["keyword"], "shop": row.get("shop", "") or "—"}
            cheapest = None
            if results_store[i]["status"] == "done":
                d = results_store[i]["data"]
                cheapest = d.get("cheapest")
                for pname in active_platforms:
                    r = next((x for x in d.get("results", []) if x["platform"] == pname), None)
                    if r and r.get("found"):
                        price = price_range(r)
                        disc = f" (-{r['discount']}%)" if r.get("discount") else ""
                        display_row[pname] = price + disc
                    else:
                        display_row[pname] = "—"
                display_row["🏆 ถูกสุด"] = cheapest or "—"
            else:
                for pname in active_platforms:
                    display_row[pname] = "❌ Error"
                display_row["🏆 ถูกสุด"] = "—"

            rows_done.append(display_row)
            table_placeholder.dataframe(pd.DataFrame(rows_done), use_container_width=True, hide_index=True)
            progress_bar.progress((i + 1) / total, text=f"{i+1}/{total} รายการ")
            time.sleep(0.2)

        status_text.success(f"✅ ค้นหาเสร็จทั้งหมด {total} รายการ!")

        # ── Export CSV ──
        export_rows = []
        for i, row in df.iterrows():
            export_row = {"keyword": row["keyword"], "shop": row.get("shop", "")}
            if results_store[i] and results_store[i]["status"] == "done":
                d = results_store[i]["data"]
                for pname in active_platforms:
                    r = next((x for x in d.get("results", []) if x["platform"] == pname), None)
                    if r and r.get("found"):
                        export_row[f"{pname}_minPrice"] = r.get("minPrice")
                        export_row[f"{pname}_maxPrice"] = r.get("maxPrice")
                        export_row[f"{pname}_discount%"] = r.get("discount")
                        export_row[f"{pname}_item"] = r.get("topItem")
                        export_row[f"{pname}_url"] = r.get("url")
                    else:
                        export_row[f"{pname}_minPrice"] = None
                export_row["cheapest"] = d.get("cheapest")
            export_rows.append(export_row)

        df_export = pd.DataFrame(export_rows)
        csv_bytes = df_export.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            "⬇️ Export ผลลัพธ์ CSV",
            data=csv_bytes,
            file_name="price_comparison_results.csv",
            mime="text/csv",
            use_container_width=True,
        )

else:
    # Empty state
    st.info("📂 อัปโหลดไฟล์ CSV หรือ Excel เพื่อเริ่มต้นใช้งาน")
    st.markdown("""
**รูปแบบไฟล์ที่รองรับ:**

| keyword (บังคับ) | shop (ไม่บังคับ) |
|---|---|
| Botox | Nirunda Clinic |
| ฟอกสีฟัน | Dental Signature |
| Laser CO2 | |
| นวดหน้า | Let Me Skin |
""")
