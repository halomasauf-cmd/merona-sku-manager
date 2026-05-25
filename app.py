"""
MERONA SKU MANAGER — Streamlit App + Supabase Persistence
Install : pip3 install streamlit pandas openpyxl plotly supabase
Run     : streamlit run app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import json, gzip, io, os
from datetime import datetime, date

# ── PAGE CONFIG ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="Merona SKU Manager",
    page_icon="💄",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.stDataFrame { font-size: 13px; }
.save-badge {
    background: #2E7D32; color: white;
    padding: 4px 12px; border-radius: 20px;
    font-size: 12px; font-weight: 500;
}
</style>
""", unsafe_allow_html=True)

# ── SUPABASE ─────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    """Initialize Supabase client. Returns None if not configured."""
    try:
        from supabase import create_client
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception:
        return None

def db_save(key: str, df: pd.DataFrame | None):
    """Save DataFrame to Supabase. Silently fails if DB not available."""
    sb = get_supabase()
    if sb is None:
        return False
    try:
        value = df.to_dict('records') if df is not None else None
        sb.table("merona_data").upsert({
            "key":        key,
            "value":      value,
            "updated_at": datetime.now().isoformat()
        }).execute()
        return True
    except Exception as e:
        st.toast(f"DB save failed: {e}", icon="⚠️")
        return False

def db_save_str(key: str, value):
    """Save string/dict to Supabase."""
    sb = get_supabase()
    if sb is None:
        return False
    try:
        sb.table("merona_data").upsert({
            "key":        key,
            "value":      value,
            "updated_at": datetime.now().isoformat()
        }).execute()
        return True
    except Exception:
        return False

def db_load(key: str) -> pd.DataFrame | None:
    """Load DataFrame from Supabase. Returns None if not found."""
    sb = get_supabase()
    if sb is None:
        return None
    try:
        result = sb.table("merona_data").select("value").eq("key", key).execute()
        if result.data and result.data[0]["value"]:
            return pd.DataFrame(result.data[0]["value"])
        return None
    except Exception:
        return None

def db_load_raw(key: str):
    """Load raw value from Supabase."""
    sb = get_supabase()
    if sb is None:
        return None
    try:
        result = sb.table("merona_data").select("value,updated_at").eq("key", key).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception:
        return None

def db_load_timestamp(key: str) -> str | None:
    """Get last updated timestamp for a key."""
    sb = get_supabase()
    if sb is None:
        return None
    try:
        result = sb.table("merona_data").select("updated_at").eq("key", key).execute()
        if result.data and result.data[0]["updated_at"]:
            dt = result.data[0]["updated_at"]
            return dt[:16].replace("T", " ")
        return None
    except Exception:
        return None

# ── BASELINE DATA ────────────────────────────────────────────────────
@st.cache_data
def load_baseline():
    gz_path = os.path.join(os.path.dirname(__file__), 'baseline_data.gz')
    if os.path.exists(gz_path):
        with gzip.open(gz_path, 'rb') as f:
            data = json.loads(f.read().decode())
        df = pd.DataFrame(data)
        df['sku'] = df['sku'].astype(str)
        return df
    return pd.DataFrame()

# ── SETTINGS ─────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    'period_days': 21, 'lead_time': 3,
    'buffer_days': 7,  'horizon':   7,
    'fast_thr':   20,  'med_thr':   5,
}

def get_settings():
    # Priority: session_state > DB > defaults
    if 'settings' in st.session_state and st.session_state['settings']:
        return {**DEFAULT_SETTINGS, **st.session_state['settings']}
    return {**DEFAULT_SETTINGS, **st.session_state.get('settings', {})}

# ── INIT: load from DB on first run ──────────────────────────────────
if 'db_loaded' not in st.session_state:
    st.session_state['db_loaded'] = True
    # Load persisted data from DB
    sales_from_db = db_load('sales_data')
    stock_from_db = db_load('stock_data')
    if sales_from_db is not None and len(sales_from_db) > 0:
        st.session_state['sales_data']  = sales_from_db
    if stock_from_db is not None and len(stock_from_db) > 0:
        st.session_state['stock_data']  = stock_from_db
    # Load settings
    raw_settings = db_load_raw('settings')
    if raw_settings and raw_settings.get('value'):
        st.session_state['settings'] = raw_settings['value']
    # Load last import timestamp
    ts_sales = db_load_timestamp('sales_data')
    ts_stock  = db_load_timestamp('stock_data')
    if ts_sales or ts_stock:
        st.session_state['last_import'] = ts_sales or ts_stock

if 'sales_data' not in st.session_state:
    st.session_state['sales_data'] = None
if 'stock_data' not in st.session_state:
    st.session_state['stock_data'] = None
if 'settings' not in st.session_state:
    st.session_state['settings'] = {}

# ── PROCESSING FUNCTIONS ─────────────────────────────────────────────
def parse_uploaded(file):
    try:
        if file.name.endswith('.csv'):
            df = pd.read_csv(file, encoding='utf-8-sig')
        else:
            df = pd.read_excel(file)
        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
        return df, None
    except Exception as e:
        return None, str(e)

def find_col(df, aliases):
    for a in aliases:
        if a in df.columns: return a
    return None

def process_sales(df):
    sku_col = find_col(df, ['item_sku','sku','item_code','kode_produk','barcode'])
    qty_col = find_col(df, ['qty','quantity','qty_terjual','jumlah','terjual'])
    if not sku_col or not qty_col:
        return None, f"Kolom SKU/Qty tidak ditemukan. Kolom: {list(df.columns)}"
    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
    df = df[df[qty_col] > 0]
    agg = df.groupby(sku_col)[qty_col].sum().reset_index()
    agg.columns = ['sku', 'qty']
    agg['sku'] = agg['sku'].astype(str)
    return agg, None

def process_stock(df):
    sku_col = find_col(df, ['sku','item_sku','item_code','kode_produk','barcode'])
    stk_col = find_col(df, ['stock','stok','qty','quantity','sisa_stok','sisa','jumlah'])
    if not sku_col or not stk_col:
        return None, f"Kolom SKU/Stock tidak ditemukan. Kolom: {list(df.columns)}"
    df[stk_col] = pd.to_numeric(df[stk_col], errors='coerce').fillna(0)
    agg = df.groupby(sku_col)[stk_col].sum().reset_index()
    agg.columns = ['sku', 'stock']
    agg['sku'] = agg['sku'].astype(str)
    return agg, None

def compute_planner(baseline_df, sales_df, stock_df, settings):
    S = settings
    df = baseline_df.copy()
    if sales_df is not None and len(sales_df) > 0:
        sales_df = sales_df.copy()
        sales_df['sku'] = sales_df['sku'].astype(str)
        df = df.merge(sales_df.rename(columns={'qty':'qty_new'}), on='sku', how='left')
        df['qty_effective'] = np.where(
            df['qty_new'].notna() & (df['qty_new']>0), df['qty_new'], df['qty_total'])
        df['period_days'] = S['period_days']
    else:
        df['qty_effective'] = df['qty_total']
        df['period_days']   = 21
    if stock_df is not None and len(stock_df) > 0:
        stock_df = stock_df.copy()
        stock_df['sku'] = stock_df['sku'].astype(str)
        df = df.merge(stock_df.rename(columns={'stock':'stock_new'}), on='sku', how='left')
        df['stock_effective'] = np.where(
            df['stock_new'].notna() & (df['stock_new']>=0), df['stock_new'], df['stock_total'])
    else:
        df['stock_effective'] = df['stock_total']
    df['avg_daily']   = (df['qty_effective'] / df['period_days']).round(3)
    df['days_to_out'] = np.where(df['avg_daily']>0,
                                  (df['stock_effective']/df['avg_daily']).round(1), 999)
    df['need_7d']     = np.ceil(df['avg_daily'] * (S['horizon']+S['buffer_days'])).astype(int)
    df['perlu_beli']  = np.maximum(0,
        np.ceil(df['avg_daily']*(S['horizon']+S['buffer_days']+S['lead_time'])
                - df['stock_effective'])).astype(int)
    df['po_value']    = df['perlu_beli'] * df['buy_price']
    monthly_equiv     = df['avg_daily'] * 30
    def classify(x):
        if x >= S['fast_thr']: return 'FAST MOVING'
        if x >= S['med_thr']:  return 'MEDIUM MOVING'
        if x > 0:              return 'SLOW MOVING'
        return 'NO SALES'
    df['klasifikasi'] = monthly_equiv.apply(classify)
    def status(row):
        if row['avg_daily'] == 0:                           return '⚫ NO SALES'
        d = row['days_to_out']
        if d < S['lead_time']:                              return '🔴 BELI HARI INI'
        if d < S['horizon'] + S['lead_time']:               return '🟠 BELI MINGGU INI'
        if d < (S['horizon']+S['buffer_days'])*2:           return '🟡 PANTAU'
        return '✅ AMAN'
    df['status'] = df.apply(status, axis=1)
    return df

def to_excel_bytes(df, sheet='Data'):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name=sheet)
    return buf.getvalue()

# ── COMPUTE ───────────────────────────────────────────────────────────
baseline  = load_baseline()
settings  = get_settings()
df        = compute_planner(
    baseline,
    st.session_state['sales_data'],
    st.session_state['stock_data'],
    settings
)
has_db    = get_supabase() is not None
has_sales = st.session_state['sales_data'] is not None
has_stock = st.session_state['stock_data'] is not None

# ── SIDEBAR ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💄 Merona SKU Manager")
    st.markdown("---")
    page = st.radio("Navigasi", [
        "🏠 Dashboard",
        "🚨 Purchase Planner",
        "🏆 SKU Classification",
        "📥 Import Data",
        "💤 Deadstock",
        "⚙️ Settings",
    ], label_visibility="collapsed")
    st.markdown("---")
    # DB status indicator
    if has_db:
        st.markdown("🟢 **Database:** Terhubung")
        ts_s = db_load_timestamp('sales_data')
        ts_k = db_load_timestamp('stock_data')
        if ts_s: st.caption(f"Sales tersimpan: {ts_s}")
        if ts_k: st.caption(f"Stok tersimpan: {ts_k}")
    else:
        st.markdown("🔴 **Database:** Tidak terhubung")
        st.caption("Buka Settings → setup Supabase")
    st.markdown("---")
    st.markdown("**Status Data:**")
    st.markdown(f"{'✅' if has_sales else '⬜'} Sales: {'Tersimpan di DB' if has_sales and has_db else 'Baseline Mei 2026' if not has_sales else 'Session only'}")
    st.markdown(f"{'✅' if has_stock else '⬜'} Stok: {'Tersimpan di DB' if has_stock and has_db else 'Baseline 22 Mei' if not has_stock else 'Session only'}")

# ═══════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("🏠 Dashboard")
    urgent = (df['status']=='🔴 BELI HARI INI').sum()
    buy_wk = (df['status']=='🟠 BELI MINGGU INI').sum()
    po_val = df['po_value'].sum()
    aman   = (df['status']=='✅ AMAN').sum()

    k1,k2,k3,k4,k5 = st.columns(5)
    k1.metric("Total SKU Aktif",    f"{len(df):,}")
    k2.metric("🔴 Beli Hari Ini",   f"{urgent}")
    k3.metric("🟠 Beli Minggu Ini", f"{buy_wk}")
    k4.metric("Estimasi PO",        f"Rp {po_val:,.0f}")
    k5.metric("✅ Aman",             f"{aman:,}")

    if urgent > 0:
        st.error(f"⚠️ {urgent} SKU perlu dibeli HARI INI — buka Purchase Planner!")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Status Stok")
        sc = df[df['status']!='⚫ NO SALES']['status'].value_counts().reset_index()
        sc.columns = ['Status','Jumlah']
        fig = px.pie(sc, values='Jumlah', names='Status', hole=0.4,
                     color='Status',
                     color_discrete_map={
                         '🔴 BELI HARI INI':'#B71C1C',
                         '🟠 BELI MINGGU INI':'#E65100',
                         '🟡 PANTAU':'#F9A825',
                         '✅ AMAN':'#2E7D32'})
        fig.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                          legend=dict(orientation='h',y=-0.15))
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.subheader("Klasifikasi SKU")
        kc = df['klasifikasi'].value_counts().reset_index()
        kc.columns = ['Klasifikasi','Jumlah']
        fig2 = px.bar(kc, x='Klasifikasi', y='Jumlah', text='Jumlah',
                      color='Klasifikasi',
                      color_discrete_map={
                          'FAST MOVING':'#E91E63','MEDIUM MOVING':'#E65100',
                          'SLOW MOVING':'#546E7A','NO SALES':'#9E9E9E'})
        fig2.update_traces(textposition='outside')
        fig2.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                           showlegend=False, xaxis_title='', yaxis_title='')
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Top 15 Brand — Nilai PO Dibutuhkan")
    bp = df[df['perlu_beli']>0].groupby('brand').agg(
        sku_count=('sku','count'), po_value=('po_value','sum')
    ).reset_index().sort_values('po_value', ascending=False).head(15)
    if len(bp) > 0:
        fig3 = px.bar(bp, x='brand', y='po_value',
                      text=bp['po_value'].apply(lambda x: f"Rp {x:,.0f}"),
                      color='po_value',
                      color_continuous_scale=['#FCE4EC','#E91E63','#B71C1C'])
        fig3.update_traces(textposition='outside', textfont_size=9)
        fig3.update_layout(height=320, margin=dict(t=10,b=60,l=0,r=0),
                           xaxis_tickangle=-30, coloraxis_showscale=False,
                           xaxis_title='', yaxis_title='Nilai PO (Rp)')
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.success("Semua stok aman!")

# ═══════════════════════════════════════════════════════════════════
# PURCHASE PLANNER
# ═══════════════════════════════════════════════════════════════════
elif page == "🚨 Purchase Planner":
    st.title("🚨 Purchase Planner")
    if has_db:
        ts = db_load_timestamp('sales_data')
        st.caption(f"📡 Data dari database · Sales terakhir update: {ts or 'belum ada'}")
    else:
        st.warning("Database belum terhubung — data hanya tersimpan di sesi ini. Setup Supabase di Settings.")

    cf1,cf2,cf3,cf4 = st.columns(4)
    status_filter = cf1.multiselect("Status",
        ['🔴 BELI HARI INI','🟠 BELI MINGGU INI','🟡 PANTAU','✅ AMAN','⚫ NO SALES'],
        default=['🔴 BELI HARI INI','🟠 BELI MINGGU INI'])
    brand_opts = ['Semua'] + sorted(df['brand'].dropna().unique().tolist())
    brand_filter = cf2.selectbox("Brand", brand_opts)
    klas_filter  = cf3.selectbox("Klasifikasi",
        ['Semua','FAST MOVING','MEDIUM MOVING','SLOW MOVING'])
    min_beli = cf4.number_input("Min Qty Beli", min_value=0, value=1, step=1)

    filtered = df.copy()
    if status_filter: filtered = filtered[filtered['status'].isin(status_filter)]
    if brand_filter != 'Semua': filtered = filtered[filtered['brand']==brand_filter]
    if klas_filter  != 'Semua': filtered = filtered[filtered['klasifikasi']==klas_filter]
    if min_beli > 0: filtered = filtered[filtered['perlu_beli'] >= min_beli]
    filtered = filtered.sort_values(['days_to_out','perlu_beli'], ascending=[True,False])

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("SKU ditampilkan", len(filtered))
    m2.metric("Total unit beli", f"{filtered['perlu_beli'].sum():,}")
    m3.metric("Estimasi PO",     f"Rp {filtered['po_value'].sum():,.0f}")
    m4.metric("Brand terdampak", filtered['brand'].nunique())

    show_cols = {
        'brand':'Brand','product_full':'Nama Produk','sku':'SKU',
        'stock_effective':'Stok','avg_daily':'Jual/Hari',
        'days_to_out':'Hari s/d Kosong','perlu_beli':'🛒 PERLU BELI',
        'buy_price':'Harga Beli','po_value':'Nilai PO','status':'STATUS'
    }
    show_df = filtered[list(show_cols.keys())].rename(columns=show_cols)
    show_df['Hari s/d Kosong'] = show_df['Hari s/d Kosong'].apply(
        lambda x: '∞' if x >= 999 else f'{x:.1f}')

    def color_row(row):
        c = {'🔴 BELI HARI INI':'background-color:#FFEBEE',
             '🟠 BELI MINGGU INI':'background-color:#FFF3E0',
             '🟡 PANTAU':'background-color:#FFFDE7',
             '✅ AMAN':'background-color:#E8F5E9'}.get(row.get('STATUS',''),'')
        return [c]*len(row)

    styled = show_df.style.apply(color_row, axis=1).format({
        'Stok':'%.0f','Jual/Hari':'%.2f',
        '🛒 PERLU BELI':'%.0f','Harga Beli':'Rp {:,.0f}','Nilai PO':'Rp {:,.0f}'
    })
    st.dataframe(styled, use_container_width=True, height=480)

    dl1,dl2 = st.columns(2)
    po_export = filtered[['brand','product_full','sku','stock_effective',
                           'avg_daily','days_to_out','perlu_beli',
                           'buy_price','po_value','status']].copy()
    po_export.columns = ['Brand','Nama Produk','SKU','Stok','Jual/Hari',
                          'Hari s/d Kosong','PERLU BELI','Harga Beli','Nilai PO','Status']
    dl1.download_button("⬇️ Download PO (Excel)",
        data=to_excel_bytes(po_export, 'Purchase Order'),
        file_name=f"PO_Merona_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    dl2.download_button("⬇️ Download CSV",
        data=filtered.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"purchase_planner_{date.today().strftime('%Y%m%d')}.csv",
        mime='text/csv')

# ═══════════════════════════════════════════════════════════════════
# SKU CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════
elif page == "🏆 SKU Classification":
    st.title("🏆 SKU Classification")
    cf1,cf2,cf3,cf4 = st.columns(4)
    bf = cf1.selectbox("Brand", ['Semua']+sorted(df['brand'].unique().tolist()))
    kf = cf2.selectbox("Klasifikasi", ['Semua']+df['klasifikasi'].unique().tolist())
    sf = cf3.selectbox("Status", ['Semua']+df['status'].unique().tolist())
    qf = cf4.text_input("Cari Produk/SKU","")
    filtered = df.copy()
    if bf != 'Semua': filtered = filtered[filtered['brand']==bf]
    if kf != 'Semua': filtered = filtered[filtered['klasifikasi']==kf]
    if sf != 'Semua': filtered = filtered[filtered['status']==sf]
    if qf:
        q = qf.lower()
        filtered = filtered[
            filtered['product_full'].str.lower().str.contains(q,na=False)|
            filtered['sku'].str.lower().str.contains(q,na=False)|
            filtered['brand'].str.lower().str.contains(q,na=False)]
    filtered = filtered.sort_values('avg_daily', ascending=False)
    st.caption(f"{len(filtered):,} dari {len(df):,} SKU")
    show = filtered[['brand','product_full','sku','stock_effective',
                      'avg_daily','klasifikasi','margin_pct',
                      'buy_price','sell_price','perlu_beli','status']].copy()
    show.columns = ['Brand','Produk','SKU','Stok','Jual/Hari',
                    'Klasifikasi','Margin%','Harga Beli','Harga Jual',
                    'Perlu Beli','Status']
    st.dataframe(show, use_container_width=True, height=540,
        column_config={
            'Margin%':     st.column_config.NumberColumn('Margin%', format='%.1f%%'),
            'Harga Beli':  st.column_config.NumberColumn('Harga Beli', format='Rp %d'),
            'Harga Jual':  st.column_config.NumberColumn('Harga Jual', format='Rp %d'),
            'Perlu Beli':  st.column_config.NumberColumn('Perlu Beli', format='%d unit'),
        })
    st.download_button("⬇️ Download Excel",
        data=to_excel_bytes(show,'SKU Classification'),
        file_name=f"SKU_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ═══════════════════════════════════════════════════════════════════
# IMPORT DATA
# ═══════════════════════════════════════════════════════════════════
elif page == "📥 Import Data":
    st.title("📥 Import Data")

    if has_db:
        st.success("🟢 Database terhubung — data akan tersimpan permanen dan bisa diakses semua tim.")
    else:
        st.warning("🔴 Database belum terhubung — data hanya tersimpan di sesi ini. "
                   "Setup Supabase di menu ⚙️ Settings.")

    tab1, tab2 = st.tabs(["📊 Upload Penjualan", "📦 Upload Stok"])

    with tab1:
        st.subheader("Upload Data Penjualan")
        st.markdown("""
        **Export dari POS:** Laporan → **Item Penjualan Berdasarkan Brand** → Export Excel
        Boleh upload dari 3 toko sekaligus — sistem SUMIF otomatis per SKU.
        """)
        period_input = st.number_input("Periode data (hari)?",
            min_value=1, max_value=90,
            value=st.session_state['settings'].get('period_days',21))

        sales_files = st.file_uploader("Upload file penjualan",
            accept_multiple_files=True, type=['xlsx','xls','csv'], key='su')

        if sales_files:
            all_sales, errors = [], []
            for f in sales_files:
                raw, err = parse_uploaded(f)
                if err: errors.append(f"{f.name}: {err}"); continue
                agg, err2 = process_sales(raw)
                if err2: errors.append(f"{f.name}: {err2}"); continue
                all_sales.append(agg)
                st.success(f"✓ {f.name} — {agg['sku'].nunique():,} SKU unik")
            for e in errors:
                st.error(e)
            if all_sales:
                combined = pd.concat(all_sales).groupby('sku')['qty'].sum().reset_index()
                st.info(f"Total: **{combined['sku'].nunique():,} SKU** · **{combined['qty'].sum():,} unit** terjual")
                if st.button("✅ Simpan Data Penjualan", type='primary', key='save_sales'):
                    st.session_state['sales_data']  = combined
                    st.session_state['settings']['period_days'] = period_input
                    # Save to DB
                    if db_save('sales_data', combined):
                        db_save_str('settings', st.session_state['settings'])
                        st.success("✅ Data penjualan tersimpan di database! Semua tim bisa lihat sekarang.")
                    else:
                        st.success("✅ Data tersimpan di sesi ini.")
                        if not has_db:
                            st.info("💡 Setup Supabase di Settings untuk simpan permanen.")
                    st.session_state['last_import'] = datetime.now().strftime('%d/%m/%Y %H:%M')
                    st.balloons()

    with tab2:
        st.subheader("Upload Data Stok Terkini")
        st.markdown("""
        **Export dari POS:** Laporan → **Sisa Stok Produk** → Export Excel
        Boleh upload dari 3 toko sekaligus.
        """)
        stock_files = st.file_uploader("Upload file stok",
            accept_multiple_files=True, type=['xlsx','xls','csv'], key='ku')

        if stock_files:
            all_stocks = []
            for f in stock_files:
                raw, err = parse_uploaded(f)
                if err: st.error(f"{f.name}: {err}"); continue
                agg, err2 = process_stock(raw)
                if err2: st.error(f"{f.name}: {err2}"); continue
                all_stocks.append(agg)
                st.success(f"✓ {f.name} — {agg['sku'].nunique():,} SKU · {agg['stock'].sum():,.0f} unit")
            if all_stocks:
                combined_stock = pd.concat(all_stocks).groupby('sku')['stock'].sum().reset_index()
                st.info(f"Total: **{combined_stock['sku'].nunique():,} SKU** · **{combined_stock['stock'].sum():,.0f} unit** stok")
                if st.button("✅ Simpan Data Stok", type='primary', key='save_stock'):
                    st.session_state['stock_data'] = combined_stock
                    if db_save('stock_data', combined_stock):
                        st.success("✅ Data stok tersimpan di database! Semua tim bisa lihat sekarang.")
                    else:
                        st.success("✅ Data stok tersimpan di sesi ini.")
                    st.balloons()

# ═══════════════════════════════════════════════════════════════════
# DEADSTOCK
# ═══════════════════════════════════════════════════════════════════
elif page == "💤 Deadstock":
    st.title("💤 Deadstock — Modal Terikat")
    dead = df[(df['avg_daily']==0) & (df['stock_effective']>0)].copy()
    dead['nilai_modal'] = dead['stock_effective'] * dead['buy_price']
    dead = dead.sort_values('nilai_modal', ascending=False)
    total = dead['nilai_modal'].sum()
    m1,m2,m3 = st.columns(3)
    m1.metric("Total SKU Deadstock", f"{len(dead):,}")
    m2.metric("Total Modal Terikat",  f"Rp {total:,.0f}")
    m3.metric("Rata-rata per SKU",    f"Rp {total/max(len(dead),1):,.0f}")
    st.warning(f"Rp {total:,.0f} modal terikat. Pertimbangkan: promosi besar, bundle, atau retur ke supplier.")

    bf = st.selectbox("Filter Brand", ['Semua']+sorted(dead['brand'].unique().tolist()))
    if bf != 'Semua': dead = dead[dead['brand']==bf]

    if len(dead) > 0:
        bs = dead.groupby('brand').agg(
            sku_count=('sku','count'), modal=('nilai_modal','sum')
        ).reset_index().sort_values('modal', ascending=False).head(20)
        fig = px.bar(bs, x='brand', y='modal',
                     text=bs['modal'].apply(lambda x: f"Rp {x:,.0f}"),
                     color='modal', color_continuous_scale=['#E8EAF6','#6A1B9A'])
        fig.update_traces(textposition='outside', textfont_size=9)
        fig.update_layout(height=280, xaxis_tickangle=-30, coloraxis_showscale=False,
                          xaxis_title='', margin=dict(t=10,b=60))
        st.plotly_chart(fig, use_container_width=True)

    show = dead[['brand','product_full','sku','stock_effective','buy_price','nilai_modal']].copy()
    show.columns = ['Brand','Nama Produk','SKU','Stok','Harga Beli','Nilai Modal']
    st.dataframe(show, use_container_width=True, height=400,
        column_config={
            'Harga Beli':  st.column_config.NumberColumn(format='Rp %d'),
            'Nilai Modal': st.column_config.NumberColumn(format='Rp %d'),
        })
    st.download_button("⬇️ Download Daftar Deadstock",
        data=to_excel_bytes(show,'Deadstock'),
        file_name=f"Deadstock_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ═══════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("⚙️ Settings")

    # Supabase setup section
    st.subheader("🗄️ Setup Database (Supabase)")
    if has_db:
        st.success("🟢 Database sudah terhubung dan berfungsi!")
    else:
        st.error("🔴 Database belum terhubung.")
        with st.expander("📖 Cara Setup Supabase (3 menit)", expanded=True):
            st.markdown("""
**Langkah 1 — Buat akun Supabase gratis**
Buka **supabase.com** → Sign Up → buat project baru (gratis)

**Langkah 2 — Buat tabel**
Di Supabase → klik **SQL Editor** → paste SQL ini → klik **Run:**
```sql
CREATE TABLE IF NOT EXISTS merona_data (
  key        TEXT PRIMARY KEY,
  value      JSONB,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE merona_data ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow_all" ON merona_data
  FOR ALL USING (true) WITH CHECK (true);
```

**Langkah 3 — Ambil credentials**
- Klik **Settings** → **API**
- Copy: **Project URL** dan **anon/public key**

**Langkah 4 — Tambah ke Streamlit Secrets**
Di Streamlit Cloud → app kamu → **⋮** → **Settings** → **Secrets** → paste ini:
```toml
[supabase]
url = "https://xxxxxx.supabase.co"
key = "eyJhbGciO..."
```
Klik **Save** → app restart → database terhubung!
            """)

    st.markdown("---")
    st.subheader("⚙️ Parameter Sistem")
    with st.form("settings_form"):
        c1,c2 = st.columns(2)
        with c1:
            period = st.number_input("Periode Data (hari)", value=settings['period_days'], min_value=1, max_value=90)
            lead   = st.number_input("Lead Time Supplier (hari)", value=settings['lead_time'], min_value=1, max_value=30)
            buf    = st.number_input("Buffer Stok (hari)", value=settings['buffer_days'], min_value=0, max_value=60)
        with c2:
            horizon= st.number_input("Horizon Perencanaan (hari)", value=settings['horizon'], min_value=1, max_value=30)
            fast_t = st.number_input("Threshold Fast Moving (unit/bln)", value=settings['fast_thr'], min_value=1)
            med_t  = st.number_input("Threshold Medium Moving (unit/bln)", value=settings['med_thr'], min_value=1)
        if st.form_submit_button("💾 Simpan Settings", type='primary'):
            new_settings = {
                'period_days':period, 'lead_time':lead,
                'buffer_days':buf,    'horizon':horizon,
                'fast_thr':fast_t,    'med_thr':med_t,
            }
            st.session_state['settings'] = new_settings
            if db_save_str('settings', new_settings):
                st.success("Settings tersimpan di database!")
            else:
                st.success("Settings tersimpan di sesi ini.")
            st.rerun()

    st.markdown("---")
    st.subheader("Reset Data")
    c1,c2 = st.columns(2)
    if c1.button("🗑 Reset Sales ke Baseline"):
        st.session_state['sales_data'] = None
        db_save('sales_data', None)
        st.success("Reset!")
    if c2.button("🗑 Reset Stok ke Baseline"):
        st.session_state['stock_data'] = None
        db_save('stock_data', None)
        st.success("Reset!")
