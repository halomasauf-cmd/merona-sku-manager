"""
MERONA SKU MANAGER — Streamlit App + Supabase + Velocity×Margin Matrix
Install : pip3 install streamlit pandas openpyxl plotly supabase
Run     : streamlit run app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import json, gzip, io, os
from datetime import datetime, date

st.set_page_config(
    page_title="Merona SKU Manager",
    page_icon="💄",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.stDataFrame { font-size: 13px; }
</style>
""", unsafe_allow_html=True)

# ── SUPABASE ─────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    try:
        from supabase import create_client
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception:
        return None

def db_save(key, df):
    sb = get_supabase()
    if sb is None: return False
    try:
        value = df.to_dict('records') if df is not None else None
        sb.table("merona_data").upsert({
            "key": key, "value": value,
            "updated_at": datetime.now().isoformat()
        }).execute()
        return True
    except Exception as e:
        st.toast(f"DB error: {e}", icon="⚠️")
        return False

def db_save_str(key, value):
    sb = get_supabase()
    if sb is None: return False
    try:
        sb.table("merona_data").upsert({
            "key": key, "value": value,
            "updated_at": datetime.now().isoformat()
        }).execute()
        return True
    except Exception: return False

def db_load(key):
    sb = get_supabase()
    if sb is None: return None
    try:
        r = sb.table("merona_data").select("value").eq("key", key).execute()
        if r.data and r.data[0]["value"]:
            return pd.DataFrame(r.data[0]["value"])
        return None
    except Exception: return None

def db_load_raw(key):
    sb = get_supabase()
    if sb is None: return None
    try:
        r = sb.table("merona_data").select("value,updated_at").eq("key", key).execute()
        return r.data[0] if r.data else None
    except Exception: return None

def db_ts(key):
    sb = get_supabase()
    if sb is None: return None
    try:
        r = sb.table("merona_data").select("updated_at").eq("key", key).execute()
        if r.data and r.data[0]["updated_at"]:
            return r.data[0]["updated_at"][:16].replace("T", " ")
        return None
    except Exception: return None

# ── BASELINE DATA ────────────────────────────────────────────────────
@st.cache_data
def load_baseline():
    gz = os.path.join(os.path.dirname(__file__), 'baseline_data.gz')
    if os.path.exists(gz):
        with gzip.open(gz, 'rb') as f:
            data = json.loads(f.read().decode())
        df = pd.DataFrame(data)
        df['sku'] = df['sku'].astype(str)
        return df
    return pd.DataFrame()

# ── SETTINGS ─────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    'period_days': 21, 'lead_time': 3,
    'buffer_days': 7,  'horizon':  7,
    'fast_thr':   20,  'med_thr':  5,
    'margin_thr': 25,  # NEW: threshold margin tebal vs tipis (%)
}

def get_settings():
    return {**DEFAULT_SETTINGS, **st.session_state.get('settings', {})}

# ── PRIORITY CLASS SYSTEM ────────────────────────────────────────────
PRIORITY_ORDER = {
    '⭐ STARS':       1,
    '📦 VOLUME':      2,
    '💰 CASH COW':    3,
    '🔵 STANDARD':    4,
    '💎 HIDDEN GEM':  5,
    '⚠️ REVIEW':      6,
    '💤 NO SALES':    7,
}

PRIORITY_COLOR = {
    '⭐ STARS':      '#F59E0B',
    '📦 VOLUME':     '#3B82F6',
    '💰 CASH COW':   '#10B981',
    '🔵 STANDARD':   '#6B7280',
    '💎 HIDDEN GEM': '#8B5CF6',
    '⚠️ REVIEW':     '#EF4444',
    '💤 NO SALES':   '#D1D5DB',
}

PRIORITY_BG = {
    '⭐ STARS':      'background-color:#FFFBEB',
    '📦 VOLUME':     'background-color:#EFF6FF',
    '💰 CASH COW':   'background-color:#ECFDF5',
    '🔵 STANDARD':   'background-color:#F9FAFB',
    '💎 HIDDEN GEM': 'background-color:#F5F3FF',
    '⚠️ REVIEW':     'background-color:#FEF2F2',
    '💤 NO SALES':   '',
}

PRIORITY_DESC = {
    '⭐ STARS':      'Fast + Margin Tebal — JANGAN KOSONG',
    '📦 VOLUME':     'Fast + Margin Tipis — Negosiasi harga beli',
    '💰 CASH COW':   'Medium + Margin Tebal — Push penjualan',
    '🔵 STANDARD':   'Medium + Margin Tipis — Restock rutin',
    '💎 HIDDEN GEM': 'Slow + Margin Tebal — Promosikan lebih',
    '⚠️ REVIEW':     'Slow + Margin Tipis — Pertimbangkan drop',
    '💤 NO SALES':   'Belum ada penjualan',
}

BUFFER_BY_PRIORITY = {
    '⭐ STARS':      1.00,
    '📦 VOLUME':     1.00,
    '💰 CASH COW':   0.50,
    '🔵 STANDARD':   0.50,
    '💎 HIDDEN GEM': 0.20,
    '⚠️ REVIEW':     0.10,
    '💤 NO SALES':   0.00,
}

# ── INIT SESSION ─────────────────────────────────────────────────────
if 'db_loaded' not in st.session_state:
    st.session_state['db_loaded'] = True
    for key in ['sales_data', 'stock_data']:
        loaded = db_load(key)
        if loaded is not None and len(loaded) > 0:
            st.session_state[key] = loaded
    raw = db_load_raw('settings')
    if raw and raw.get('value'):
        st.session_state['settings'] = raw['value']

if 'sales_data' not in st.session_state: st.session_state['sales_data'] = None
if 'stock_data' not in st.session_state: st.session_state['stock_data'] = None
if 'settings'   not in st.session_state: st.session_state['settings']   = {}

# ── PROCESSING ───────────────────────────────────────────────────────
def parse_file(file):
    try:
        df = pd.read_csv(file, encoding='utf-8-sig') if file.name.endswith('.csv') else pd.read_excel(file)
        df.columns = [str(c).strip().lower().replace(' ','_') for c in df.columns]
        return df, None
    except Exception as e:
        return None, str(e)

def find_col(df, aliases):
    for a in aliases:
        if a in df.columns: return a
    return None

def process_sales(df):
    sku = find_col(df, ['item_sku','sku','item_code','kode_produk','barcode'])
    qty = find_col(df, ['qty','quantity','qty_terjual','jumlah','terjual'])
    if not sku or not qty:
        return None, f"Kolom SKU/Qty tidak ditemukan. Kolom: {list(df.columns)}"
    df[qty] = pd.to_numeric(df[qty], errors='coerce').fillna(0)
    agg = df[df[qty]>0].groupby(sku)[qty].sum().reset_index()
    agg.columns = ['sku','qty']
    agg['sku'] = agg['sku'].astype(str)
    return agg, None

def process_stock(df):
    sku = find_col(df, ['sku','item_sku','item_code','kode_produk','barcode'])
    stk = find_col(df, ['stock','stok','qty','quantity','sisa_stok','sisa','jumlah'])
    if not sku or not stk:
        return None, f"Kolom SKU/Stock tidak ditemukan. Kolom: {list(df.columns)}"
    df[stk] = pd.to_numeric(df[stk], errors='coerce').fillna(0)
    agg = df.groupby(sku)[stk].sum().reset_index()
    agg.columns = ['sku','stock']
    agg['sku'] = agg['sku'].astype(str)
    return agg, None

def compute(baseline, sales_df, stock_df, S):
    df = baseline.copy()

    # Merge sales
    if sales_df is not None and len(sales_df):
        sales_df = sales_df.copy()
        sales_df['sku'] = sales_df['sku'].astype(str)
        df = df.merge(sales_df.rename(columns={'qty':'qty_new'}), on='sku', how='left')
        df['qty_eff']  = np.where(df['qty_new'].notna()&(df['qty_new']>0), df['qty_new'], df['qty_total'])
        df['period']   = S['period_days']
    else:
        df['qty_eff'] = df['qty_total']
        df['period']  = 21

    # Merge stock
    if stock_df is not None and len(stock_df):
        stock_df = stock_df.copy()
        stock_df['sku'] = stock_df['sku'].astype(str)
        df = df.merge(stock_df.rename(columns={'stock':'stock_new'}), on='sku', how='left')
        df['stock_eff'] = np.where(df['stock_new'].notna()&(df['stock_new']>=0), df['stock_new'], df['stock_total'])
    else:
        df['stock_eff'] = df['stock_total']

    df['avg_daily']   = (df['qty_eff'] / df['period']).round(3)
    df['monthly_est'] = df['avg_daily'] * 30

    # ── PRIORITY CLASS (Velocity × Margin) ───────────────────────────
    def priority_class(row):
        m  = row['avg_daily'] * 30
        mg = row.get('margin_pct', 0)
        if m == 0: return '💤 NO SALES'
        hi_margin = mg >= S['margin_thr']
        if   m >= S['fast_thr'] and hi_margin:  return '⭐ STARS'
        elif m >= S['fast_thr']:                 return '📦 VOLUME'
        elif m >= S['med_thr']  and hi_margin:  return '💰 CASH COW'
        elif m >= S['med_thr']:                  return '🔵 STANDARD'
        elif hi_margin:                          return '💎 HIDDEN GEM'
        else:                                    return '⚠️ REVIEW'

    df['priority_class'] = df.apply(priority_class, axis=1)
    df['priority_num']   = df['priority_class'].map(PRIORITY_ORDER).fillna(9)

    # Buffer based on priority class
    df['buffer_pct']  = df['priority_class'].map(BUFFER_BY_PRIORITY).fillna(0.20)
    df['buffer_unit'] = np.ceil(df['avg_daily'] * 30 * df['buffer_pct']).astype(int)

    # Days to stockout & buy quantity
    df['days_to_out'] = np.where(df['avg_daily']>0,
                                  (df['stock_eff']/df['avg_daily']).round(1), 999)
    df['perlu_beli']  = np.maximum(0,
        np.ceil(df['avg_daily']*(S['horizon']+S['buffer_days']+S['lead_time'])
                - df['stock_eff'])).astype(int)
    df['po_value']    = df['perlu_beli'] * df['buy_price']

    # Urgency status
    def status(row):
        if row['avg_daily'] == 0: return '⚫ NO SALES'
        d = row['days_to_out']
        if d < S['lead_time']:                    return '🔴 BELI HARI INI'
        if d < S['horizon'] + S['lead_time']:     return '🟠 BELI MINGGU INI'
        if d < (S['horizon']+S['buffer_days'])*2: return '🟡 PANTAU'
        return '✅ AMAN'

    df['status'] = df.apply(status, axis=1)

    # Combined sort key: priority_num first, then days_to_out
    df['sort_key'] = df['priority_num'] * 1000 + df['days_to_out'].clip(upper=999)

    return df

def to_excel(df, sheet='Data'):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name=sheet)
    return buf.getvalue()

# ── COMPUTE ───────────────────────────────────────────────────────────
baseline = load_baseline()
S        = get_settings()
df       = compute(baseline, st.session_state['sales_data'], st.session_state['stock_data'], S)
has_db   = get_supabase() is not None
has_sales= st.session_state['sales_data'] is not None
has_stock= st.session_state['stock_data'] is not None

# ── SIDEBAR ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💄 Merona SKU Manager")
    st.markdown("---")
    page = st.radio("Navigasi", [
        "🏠 Dashboard",
        "🚨 Purchase Planner",
        "🏆 SKU Matrix",
        "📥 Import Data",
        "💤 Deadstock",
        "⚙️ Settings",
    ], label_visibility="collapsed")
    st.markdown("---")
    st.markdown(f"{'🟢' if has_db else '🔴'} **Database:** {'Terhubung' if has_db else 'Tidak terhubung'}")
    if has_db:
        ts_s = db_ts('sales_data')
        ts_k = db_ts('stock_data')
        if ts_s: st.caption(f"Sales: {ts_s}")
        if ts_k: st.caption(f"Stok: {ts_k}")
    else:
        st.caption("Setup Supabase di ⚙️ Settings")
    st.markdown("---")
    st.markdown("**Status Data:**")
    st.markdown(f"{'✅' if has_sales else '⬜'} Sales: {'Terupdate' if has_sales else 'Baseline Mei 2026'}")
    st.markdown(f"{'✅' if has_stock else '⬜'} Stok: {'Terupdate' if has_stock else 'Baseline 22 Mei'}")

# ═══════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("🏠 Dashboard")

    urgent  = (df['status']=='🔴 BELI HARI INI').sum()
    buy_wk  = (df['status']=='🟠 BELI MINGGU INI').sum()
    po_val  = df['po_value'].sum()
    stars_n = (df['priority_class']=='⭐ STARS').sum()

    k1,k2,k3,k4,k5 = st.columns(5)
    k1.metric("Total SKU Aktif",    f"{len(df):,}")
    k2.metric("🔴 Beli Hari Ini",   f"{urgent}")
    k3.metric("🟠 Beli Minggu Ini", f"{buy_wk}")
    k4.metric("⭐ Stars SKU",        f"{stars_n}")
    k5.metric("Estimasi PO",        f"Rp {po_val:,.0f}")

    if urgent > 0:
        st.error(f"⚠️ {urgent} SKU perlu dibeli HARI INI — buka Purchase Planner!")

    st.markdown("---")
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Priority Matrix")
        pc = df[df['priority_class']!='💤 NO SALES']['priority_class'].value_counts().reset_index()
        pc.columns = ['Kategori','Jumlah']
        pc['Urutan'] = pc['Kategori'].map(PRIORITY_ORDER)
        pc = pc.sort_values('Urutan')
        fig = px.bar(pc, x='Kategori', y='Jumlah', text='Jumlah',
                     color='Kategori',
                     color_discrete_map=PRIORITY_COLOR)
        fig.update_traces(textposition='outside')
        fig.update_layout(height=280, showlegend=False,
                          margin=dict(t=10,b=60,l=0,r=0),
                          xaxis_title='', yaxis_title='',
                          xaxis_tickangle=-20)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("Status Stok")
        sc = df[df['status']!='⚫ NO SALES']['status'].value_counts().reset_index()
        sc.columns = ['Status','Jumlah']
        fig2 = px.pie(sc, values='Jumlah', names='Status', hole=0.4,
                      color='Status',
                      color_discrete_map={
                          '🔴 BELI HARI INI':'#B71C1C','🟠 BELI MINGGU INI':'#E65100',
                          '🟡 PANTAU':'#F9A825','✅ AMAN':'#2E7D32'})
        fig2.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                           legend=dict(orientation='h',y=-0.2))
        st.plotly_chart(fig2, use_container_width=True)

    # Stars vs Volume breakdown
    st.subheader("⭐ STARS vs 📦 VOLUME — Top 15 Brand")
    brand_matrix = df[df['priority_class'].isin(['⭐ STARS','📦 VOLUME'])].groupby(
        ['brand','priority_class']
    ).agg(sku_count=('sku','count'), po_value=('po_value','sum')).reset_index()
    brand_top = brand_matrix.groupby('brand')['sku_count'].sum().nlargest(15).index
    brand_matrix = brand_matrix[brand_matrix['brand'].isin(brand_top)]
    if len(brand_matrix):
        fig3 = px.bar(brand_matrix, x='brand', y='sku_count', color='priority_class',
                      color_discrete_map=PRIORITY_COLOR, text='sku_count',
                      barmode='stack')
        fig3.update_traces(textposition='inside')
        fig3.update_layout(height=300, margin=dict(t=10,b=60,l=0,r=0),
                           xaxis_tickangle=-30, xaxis_title='', yaxis_title='Jumlah SKU',
                           legend_title='')
        st.plotly_chart(fig3, use_container_width=True)
        st.caption("Brand dengan banyak VOLUME (biru) = kandidat negosiasi harga beli ke supplier")

# ═══════════════════════════════════════════════════════════════════
# PURCHASE PLANNER
# ═══════════════════════════════════════════════════════════════════
elif page == "🚨 Purchase Planner":
    st.title("🚨 Purchase Planner")
    if has_db:
        ts = db_ts('sales_data')
        st.caption(f"📡 Data dari database · Sales terakhir update: {ts or 'belum ada'}")
    else:
        st.warning("Database belum terhubung — data hanya di sesi ini. Setup Supabase di ⚙️ Settings.")

    cf1,cf2,cf3,cf4 = st.columns(4)
    status_f = cf1.multiselect("Status Urgensi",
        ['🔴 BELI HARI INI','🟠 BELI MINGGU INI','🟡 PANTAU','✅ AMAN','⚫ NO SALES'],
        default=['🔴 BELI HARI INI','🟠 BELI MINGGU INI'])
    priority_f = cf2.multiselect("Priority Class",
        list(PRIORITY_ORDER.keys())[:-1],
        default=['⭐ STARS','📦 VOLUME','💰 CASH COW'])
    brand_f = cf3.selectbox("Brand",
        ['Semua'] + sorted(df['brand'].dropna().unique().tolist()))
    min_beli = cf4.number_input("Min Qty Beli", min_value=0, value=1)

    filtered = df.copy()
    if status_f:    filtered = filtered[filtered['status'].isin(status_f)]
    if priority_f:  filtered = filtered[filtered['priority_class'].isin(priority_f)]
    if brand_f != 'Semua': filtered = filtered[filtered['brand']==brand_f]
    if min_beli > 0: filtered = filtered[filtered['perlu_beli']>=min_beli]

    # Sort: priority first, then days_to_out
    filtered = filtered.sort_values('sort_key', ascending=True)

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("SKU ditampilkan",  len(filtered))
    m2.metric("Total unit beli",  f"{filtered['perlu_beli'].sum():,}")
    m3.metric("Estimasi PO",      f"Rp {filtered['po_value'].sum():,.0f}")
    m4.metric("Brand terdampak",  filtered['brand'].nunique())

    show_cols = {
        'priority_class': 'Priority',
        'brand':          'Brand',
        'product_full':   'Nama Produk',
        'sku':            'SKU',
        'stock_eff':      'Stok',
        'avg_daily':      'Jual/Hari',
        'days_to_out':    'Hari s/d Kosong',
        'perlu_beli':     '🛒 PERLU BELI',
        'buy_price':      'Harga Beli',
        'margin_pct':     'Margin%',
        'po_value':       'Nilai PO',
        'status':         'Status Urgensi',
    }
    show_df = filtered[list(show_cols.keys())].rename(columns=show_cols).copy()
    show_df['Hari s/d Kosong'] = show_df['Hari s/d Kosong'].apply(
        lambda x: '∞' if x >= 999 else f'{x:.1f}')

    def color_row(row):
        bg = PRIORITY_BG.get(row.get('Priority',''), '')
        return [bg] * len(row)

    styled = show_df.style.apply(color_row, axis=1).format({
        'Stok':         '{:.0f}',
        'Jual/Hari':    '{:.2f}',
        '🛒 PERLU BELI':'{:.0f}',
        'Harga Beli':   'Rp {:,.0f}',
        'Margin%':      '{:.1f}%',
        'Nilai PO':     'Rp {:,.0f}',
    })
    st.dataframe(styled, use_container_width=True, height=500)

    # Legend
    with st.expander("📖 Arti Priority Class"):
        for k, v in PRIORITY_DESC.items():
            if k != '💤 NO SALES':
                st.markdown(f"**{k}** — {v}")

    dl1,dl2 = st.columns(2)
    po_export = filtered[['priority_class','brand','product_full','sku',
                           'stock_eff','avg_daily','days_to_out',
                           'perlu_beli','buy_price','margin_pct','po_value','status']].copy()
    po_export.columns = ['Priority','Brand','Produk','SKU','Stok','Jual/Hari',
                          'Hari s/d Kosong','PERLU BELI','Harga Beli','Margin%','Nilai PO','Status']
    dl1.download_button("⬇️ Download PO (Excel)",
        data=to_excel(po_export, 'Purchase Order'),
        file_name=f"PO_Merona_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    dl2.download_button("⬇️ Download CSV",
        data=filtered.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"po_{date.today().strftime('%Y%m%d')}.csv",
        mime='text/csv')

# ═══════════════════════════════════════════════════════════════════
# SKU MATRIX
# ═══════════════════════════════════════════════════════════════════
elif page == "🏆 SKU Matrix":
    st.title("🏆 SKU Matrix — Velocity × Margin")

    st.markdown(f"""
    | Kategori | Definisi | Buffer | Strategi |
    |---|---|---|---|
    | ⭐ **STARS** | Fast + Margin ≥{S['margin_thr']}% | 100% | Jangan pernah kosong |
    | 📦 **VOLUME** | Fast + Margin <{S['margin_thr']}% | 100% | Negosiasi harga beli |
    | 💰 **CASH COW** | Medium + Margin ≥{S['margin_thr']}% | 50% | Push promosi |
    | 🔵 **STANDARD** | Medium + Margin <{S['margin_thr']}% | 50% | Restock rutin |
    | 💎 **HIDDEN GEM** | Slow + Margin ≥{S['margin_thr']}% | 20% | Aktifkan dengan promo |
    | ⚠️ **REVIEW** | Slow + Margin <{S['margin_thr']}% | 10% | Pertimbangkan drop |
    """)

    st.markdown("---")
    cf1,cf2,cf3,cf4 = st.columns(4)
    pf  = cf1.multiselect("Priority Class", list(PRIORITY_ORDER.keys())[:-1],
                           default=list(PRIORITY_ORDER.keys())[:-1])
    bf  = cf2.selectbox("Brand", ['Semua']+sorted(df['brand'].unique().tolist()))
    sf  = cf3.selectbox("Status", ['Semua']+df['status'].unique().tolist())
    qf  = cf4.text_input("Cari Produk/SKU","")

    filtered = df.copy()
    if pf: filtered = filtered[filtered['priority_class'].isin(pf)]
    if bf != 'Semua': filtered = filtered[filtered['brand']==bf]
    if sf != 'Semua': filtered = filtered[filtered['status']==sf]
    if qf:
        q = qf.lower()
        filtered = filtered[
            filtered['product_full'].str.lower().str.contains(q,na=False)|
            filtered['sku'].str.lower().str.contains(q,na=False)|
            filtered['brand'].str.lower().str.contains(q,na=False)]
    filtered = filtered.sort_values('sort_key')
    st.caption(f"{len(filtered):,} dari {len(df):,} SKU")

    show = filtered[['priority_class','brand','product_full','sku',
                      'stock_eff','avg_daily','monthly_est',
                      'margin_pct','buy_price','sell_price',
                      'perlu_beli','status']].copy()
    show.columns = ['Priority','Brand','Produk','SKU','Stok','Jual/Hari',
                    'Est./Bln','Margin%','Harga Beli','Harga Jual',
                    'Perlu Beli','Status']

    def color_priority(row):
        bg = PRIORITY_BG.get(row.get('Priority',''), '')
        return [bg]*len(row)

    styled = show.style.apply(color_priority, axis=1).format({
        'Est./Bln':  '{:.1f}',
        'Jual/Hari': '{:.2f}',
        'Margin%':   '{:.1f}%',
        'Harga Beli':  'Rp {:,.0f}',
        'Harga Jual':  'Rp {:,.0f}',
        'Perlu Beli':  '{:.0f}',
    })
    st.dataframe(styled, use_container_width=True, height=540)

    st.download_button("⬇️ Download Excel",
        data=to_excel(show,'SKU Matrix'),
        file_name=f"SKU_Matrix_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ═══════════════════════════════════════════════════════════════════
# IMPORT DATA
# ═══════════════════════════════════════════════════════════════════
elif page == "📥 Import Data":
    st.title("📥 Import Data")
    if has_db:
        st.success("🟢 Database terhubung — data tersimpan permanen untuk semua tim.")
    else:
        st.warning("🔴 Database belum terhubung. Setup Supabase di ⚙️ Settings.")

    tab1, tab2 = st.tabs(["📊 Upload Penjualan", "📦 Upload Stok"])

    with tab1:
        st.subheader("Upload Data Penjualan")
        st.markdown("**Export dari POS:** Laporan → Item Penjualan Berdasarkan Brand → Export Excel")
        period_input = st.number_input("Periode data (hari)?",
            min_value=1, max_value=90, value=S['period_days'])
        files = st.file_uploader("Upload file penjualan",
            accept_multiple_files=True, type=['xlsx','xls','csv'], key='su')
        if files:
            all_s, errs = [], []
            for f in files:
                raw, err = parse_file(f)
                if err: errs.append(f"{f.name}: {err}"); continue
                agg, err2 = process_sales(raw)
                if err2: errs.append(f"{f.name}: {err2}"); continue
                all_s.append(agg)
                st.success(f"✓ {f.name} — {agg['sku'].nunique():,} SKU")
            for e in errs: st.error(e)
            if all_s:
                combined = pd.concat(all_s).groupby('sku')['qty'].sum().reset_index()
                st.info(f"Total: **{combined['sku'].nunique():,} SKU** · **{combined['qty'].sum():,} unit**")
                if st.button("✅ Simpan Data Penjualan", type='primary'):
                    st.session_state['sales_data'] = combined
                    st.session_state.setdefault('settings',{})['period_days'] = period_input
                    if db_save('sales_data', combined):
                        db_save_str('settings', st.session_state['settings'])
                        st.success("✅ Tersimpan di database! Semua tim bisa akses.")
                    else:
                        st.success("✅ Tersimpan di sesi ini.")
                    st.balloons()

    with tab2:
        st.subheader("Upload Data Stok")
        st.markdown("**Export dari POS:** Laporan → Sisa Stok Produk → Export Excel")
        files2 = st.file_uploader("Upload file stok",
            accept_multiple_files=True, type=['xlsx','xls','csv'], key='ku')
        if files2:
            all_k = []
            for f in files2:
                raw, err = parse_file(f)
                if err: st.error(f"{f.name}: {err}"); continue
                agg, err2 = process_stock(raw)
                if err2: st.error(f"{f.name}: {err2}"); continue
                all_k.append(agg)
                st.success(f"✓ {f.name} — {agg['sku'].nunique():,} SKU")
            if all_k:
                combined_k = pd.concat(all_k).groupby('sku')['stock'].sum().reset_index()
                st.info(f"Total: **{combined_k['sku'].nunique():,} SKU** · **{combined_k['stock'].sum():,.0f} unit**")
                if st.button("✅ Simpan Data Stok", type='primary'):
                    st.session_state['stock_data'] = combined_k
                    if db_save('stock_data', combined_k):
                        st.success("✅ Tersimpan di database!")
                    else:
                        st.success("✅ Tersimpan di sesi ini.")
                    st.balloons()

# ═══════════════════════════════════════════════════════════════════
# DEADSTOCK
# ═══════════════════════════════════════════════════════════════════
elif page == "💤 Deadstock":
    st.title("💤 Deadstock — Modal Terikat")
    dead = df[(df['avg_daily']==0) & (df['stock_eff']>0)].copy()
    dead['nilai_modal'] = dead['stock_eff'] * dead['buy_price']
    dead = dead.sort_values('nilai_modal', ascending=False)
    total = dead['nilai_modal'].sum()

    m1,m2,m3 = st.columns(3)
    m1.metric("Total SKU Deadstock", f"{len(dead):,}")
    m2.metric("Total Modal Terikat",  f"Rp {total:,.0f}")
    m3.metric("Rata-rata per SKU",    f"Rp {total/max(len(dead),1):,.0f}")
    st.warning(f"Rp {total:,.0f} modal tidak bergerak. Aksi: promo besar, bundle, atau retur ke supplier.")

    bf = st.selectbox("Filter Brand",['Semua']+sorted(dead['brand'].unique().tolist()))
    if bf != 'Semua': dead = dead[dead['brand']==bf]

    if len(dead):
        bs = dead.groupby('brand').agg(
            sku_count=('sku','count'), modal=('nilai_modal','sum')
        ).reset_index().sort_values('modal',ascending=False).head(20)
        fig = px.bar(bs, x='brand', y='modal',
                     text=bs['modal'].apply(lambda x: f"Rp {x:,.0f}"),
                     color='modal', color_continuous_scale=['#EDE9FE','#6D28D9'])
        fig.update_traces(textposition='outside', textfont_size=9)
        fig.update_layout(height=280, xaxis_tickangle=-30,
                          coloraxis_showscale=False, xaxis_title='',
                          margin=dict(t=10,b=60))
        st.plotly_chart(fig, use_container_width=True)

    show = dead[['brand','product_full','sku','stock_eff','buy_price','nilai_modal']].copy()
    show.columns = ['Brand','Nama Produk','SKU','Stok','Harga Beli','Nilai Modal']
    st.dataframe(show, use_container_width=True, height=400,
        column_config={
            'Harga Beli':  st.column_config.NumberColumn(format='Rp %d'),
            'Nilai Modal': st.column_config.NumberColumn(format='Rp %d'),
        })
    st.download_button("⬇️ Download Daftar Deadstock",
        data=to_excel(show,'Deadstock'),
        file_name=f"Deadstock_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ═══════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("⚙️ Settings")

    # Supabase status
    st.subheader("🗄️ Database (Supabase)")
    if has_db:
        st.success("🟢 Database terhubung!")
    else:
        st.error("🔴 Database belum terhubung.")
        with st.expander("📖 Cara Setup Supabase", expanded=True):
            st.markdown("""
**1.** Buka **supabase.com** → buat project gratis

**2.** SQL Editor → paste & Run:
```sql
CREATE TABLE IF NOT EXISTS merona_data (
  key TEXT PRIMARY KEY, value JSONB,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE merona_data ENABLE ROW LEVEL SECURITY;
CREATE POLICY "allow_all" ON merona_data FOR ALL USING (true) WITH CHECK (true);
```

**3.** Settings → API Keys → copy **Publishable key**

**4.** Streamlit Cloud → app → ⋮ → Settings → **Secrets** → paste:
```toml
[supabase]
url = "https://xxxxxx.supabase.co"
key = "sb_publishable_..."
```
**5.** Save → Reboot app
            """)

    st.markdown("---")
    st.subheader("⚙️ Parameter Sistem")
    with st.form("settings_form"):
        c1,c2 = st.columns(2)
        with c1:
            period   = st.number_input("Periode Data (hari)",       value=S['period_days'], min_value=1, max_value=90)
            lead     = st.number_input("Lead Time Supplier (hari)", value=S['lead_time'],   min_value=1, max_value=30)
            buf      = st.number_input("Buffer Stok (hari)",        value=S['buffer_days'], min_value=0, max_value=60)
        with c2:
            horizon  = st.number_input("Horizon Perencanaan (hari)",         value=S['horizon'],   min_value=1, max_value=30)
            fast_t   = st.number_input("Threshold Fast Moving (unit/bln)",   value=S['fast_thr'],  min_value=1)
            med_t    = st.number_input("Threshold Medium Moving (unit/bln)", value=S['med_thr'],   min_value=1)

        st.markdown("---")
        st.subheader("💡 Threshold Margin Tebal vs Tipis")
        margin_t = st.number_input(
            "Margin Tebal jika ≥ (%) — default 25%",
            value=S['margin_thr'], min_value=1, max_value=80,
            help="SKU dengan margin ≥ angka ini masuk kategori 'margin tebal' (STARS/CASH COW/HIDDEN GEM)")
        st.caption(f"Saat ini: margin ≥ {S['margin_thr']}% = tebal, < {S['margin_thr']}% = tipis")

        if st.form_submit_button("💾 Simpan Settings", type='primary'):
            new_s = {
                'period_days': period, 'lead_time': lead,
                'buffer_days': buf,    'horizon':   horizon,
                'fast_thr':    fast_t, 'med_thr':   med_t,
                'margin_thr':  margin_t,
            }
            st.session_state['settings'] = new_s
            if db_save_str('settings', new_s):
                st.success("✅ Settings tersimpan di database!")
            else:
                st.success("✅ Settings tersimpan di sesi ini.")
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
