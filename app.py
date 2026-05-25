"""
MERONA SKU MANAGER — Streamlit App
Install : pip3 install streamlit pandas openpyxl plotly
Run     : streamlit run app.py
Cloud   : deploy gratis di share.streamlit.io
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import json, gzip, io, os
from datetime import datetime, date

# ── PAGE CONFIG ────────────────────────────────────────────────────
st.set_page_config(
    page_title="Merona SKU Manager",
    page_icon="💄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── STYLE ──────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: white;
    border-radius: 10px;
    padding: 16px 20px;
    border-left: 4px solid #E91E63;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    margin-bottom: 8px;
}
.metric-value { font-size: 28px; font-weight: 700; color: #E91E63; }
.metric-label { font-size: 12px; color: #666; margin-top: 2px; }
.urgent-badge {
    background: #B71C1C; color: white;
    padding: 3px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 600;
}
.warn-badge {
    background: #E65100; color: white;
    padding: 3px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 600;
}
.safe-badge {
    background: #2E7D32; color: white;
    padding: 3px 10px; border-radius: 12px;
    font-size: 12px;
}
.stDataFrame { font-size: 13px; }
div[data-testid="stSidebarNav"] { display: none; }
</style>
""", unsafe_allow_html=True)

# ── BASELINE DATA LOADER ────────────────────────────────────────────
@st.cache_data
def load_baseline():
    """Load baseline data (21-day May 2026 analysis)"""
    gz_path = os.path.join(os.path.dirname(__file__), 'baseline_data.gz')
    if os.path.exists(gz_path):
        with gzip.open(gz_path, 'rb') as f:
            data = json.loads(f.read().decode())
        df = pd.DataFrame(data)
        df['sku'] = df['sku'].astype(str)
        return df
    return pd.DataFrame()

# ── SETTINGS ───────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    'period_days':  21,
    'lead_time':     3,
    'buffer_days':   7,
    'horizon':       7,
    'fast_thr':     20,
    'med_thr':       5,
}

def get_settings():
    return {**DEFAULT_SETTINGS, **st.session_state.get('settings', {})}

# ── DATA PROCESSING ─────────────────────────────────────────────────
def parse_uploaded(file, expected_cols):
    """Parse uploaded Excel/CSV file, flexible column mapping"""
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
    """Aggregate sales: return SKU → qty dict"""
    sku_col = find_col(df, ['item_sku','sku','item_code','kode_produk','barcode'])
    qty_col = find_col(df, ['qty','quantity','qty_terjual','jumlah','terjual'])
    if not sku_col or not qty_col:
        return None, f"Kolom SKU atau Qty tidak ditemukan. Kolom tersedia: {list(df.columns)}"
    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
    df = df[df[qty_col] > 0]
    agg = df.groupby(sku_col.strip())[qty_col].sum().reset_index()
    agg.columns = ['sku', 'qty']
    agg['sku'] = agg['sku'].astype(str)
    return agg, None

def process_stock(df):
    """Aggregate stock: return SKU → stock dict"""
    sku_col  = find_col(df, ['sku','item_sku','item_code','kode_produk','barcode'])
    stk_col  = find_col(df, ['stock','stok','qty','quantity','sisa_stok','sisa','jumlah'])
    if not sku_col or not stk_col:
        return None, f"Kolom SKU atau Stock tidak ditemukan. Kolom tersedia: {list(df.columns)}"
    df[stk_col] = pd.to_numeric(df[stk_col], errors='coerce').fillna(0)
    agg = df.groupby(sku_col)[stk_col].sum().reset_index()
    agg.columns = ['sku', 'stock']
    agg['sku'] = agg['sku'].astype(str)
    return agg, None

def compute_planner(baseline_df, sales_df, stock_df, settings):
    """Main computation: merge all data, compute warnings"""
    S = settings
    df = baseline_df.copy()

    # Merge latest sales
    if sales_df is not None and len(sales_df) > 0:
        df = df.merge(sales_df.rename(columns={'qty':'qty_new'}), on='sku', how='left')
        df['qty_effective'] = np.where(df['qty_new'].notna() & (df['qty_new']>0),
                                        df['qty_new'], df['qty_total'])
        df['period_days']   = S['period_days']
    else:
        df['qty_effective'] = df['qty_total']
        df['period_days']   = 21  # baseline period

    # Merge latest stock
    if stock_df is not None and len(stock_df) > 0:
        df = df.merge(stock_df.rename(columns={'stock':'stock_new'}), on='sku', how='left')
        df['stock_effective'] = np.where(df['stock_new'].notna() & (df['stock_new']>=0),
                                          df['stock_new'], df['stock_total'])
    else:
        df['stock_effective'] = df['stock_total']

    # Core calculations
    df['avg_daily']    = (df['qty_effective'] / df['period_days']).round(3)
    df['days_to_out']  = np.where(df['avg_daily']>0,
                                   (df['stock_effective']/df['avg_daily']).round(1), 999)
    df['need_7d']      = np.ceil(df['avg_daily'] * (S['horizon'] + S['buffer_days'])).astype(int)
    df['perlu_beli']   = np.maximum(0,
        np.ceil(df['avg_daily']*(S['horizon']+S['buffer_days']+S['lead_time'])
                - df['stock_effective'])).astype(int)
    df['po_value']     = df['perlu_beli'] * df['buy_price']

    # Classification (update based on monthly equiv)
    monthly_equiv = df['avg_daily'] * 30
    def classify(x): 
        if x >= S['fast_thr']: return 'FAST MOVING'
        if x >= S['med_thr']:  return 'MEDIUM MOVING'
        if x > 0:              return 'SLOW MOVING'
        return 'NO SALES'
    df['klasifikasi'] = monthly_equiv.apply(classify)

    # Status
    def status(row):
        if row['avg_daily'] == 0: return '⚫ NO SALES'
        d = row['days_to_out']
        if d < S['lead_time']:                      return '🔴 BELI HARI INI'
        if d < S['horizon'] + S['lead_time']:       return '🟠 BELI MINGGU INI'
        if d < (S['horizon']+S['buffer_days'])*2:   return '🟡 PANTAU'
        return '✅ AMAN'
    df['status'] = df.apply(status, axis=1)

    return df

def status_color(s):
    m = {'🔴 BELI HARI INI':'#B71C1C', '🟠 BELI MINGGU INI':'#E65100',
         '🟡 PANTAU':'#F9A825', '✅ AMAN':'#2E7D32', '⚫ NO SALES':'#757575'}
    return m.get(s, '#000')

def to_excel_bytes(df, sheet_name='Data'):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()

# ── INIT SESSION STATE ──────────────────────────────────────────────
if 'settings' not in st.session_state:
    st.session_state.settings = {}
if 'sales_data' not in st.session_state:
    st.session_state.sales_data = None
if 'stock_data' not in st.session_state:
    st.session_state.stock_data = None
if 'last_import' not in st.session_state:
    st.session_state.last_import = None

# ── SIDEBAR ─────────────────────────────────────────────────────────
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

    # Data status
    has_sales = st.session_state.sales_data is not None
    has_stock = st.session_state.stock_data is not None
    st.markdown("**Status Data:**")
    st.markdown(f"{'✅' if has_sales else '⬜'} Sales: {'Terupdate' if has_sales else 'Baseline Mei 2026'}")
    st.markdown(f"{'✅' if has_stock else '⬜'} Stok: {'Terupdate' if has_stock else 'Baseline 22 Mei'}")
    if st.session_state.last_import:
        st.caption(f"Update: {st.session_state.last_import}")
    st.markdown("---")
    st.caption("Merona Beauty Store")
    st.caption("3 Cabang: CG · PL · UMY")

# ── COMPUTE MAIN DATA ───────────────────────────────────────────────
baseline = load_baseline()
settings = get_settings()
df = compute_planner(
    baseline,
    st.session_state.sales_data,
    st.session_state.stock_data,
    settings
)

# ═══════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ═══════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("🏠 Dashboard")
    st.caption(f"Data: {'Baseline 1-21 Mei 2026' if not has_sales else 'Data terbaru'}  ·  "
               f"Stok: {'Baseline 22 Mei' if not has_stock else 'Data terbaru'}")

    # KPI row
    urgent  = (df['status']=='🔴 BELI HARI INI').sum()
    buy_wk  = (df['status']=='🟠 BELI MINGGU INI').sum()
    po_val  = df['po_value'].sum()
    aman    = (df['status']=='✅ AMAN').sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Total SKU Aktif", f"{len(df):,}", help="SKU dengan stok atau penjualan")
    with k2:
        st.metric("🔴 Beli Hari Ini", f"{urgent}", delta=None,
                  help="Stok habis dalam < lead time")
        if urgent > 0: st.markdown(f'<span class="urgent-badge">⚠️ {urgent} SKU</span>', unsafe_allow_html=True)
    with k3:
        st.metric("🟠 Beli Minggu Ini", f"{buy_wk}",
                  help="Stok habis dalam < 7 hari")
    with k4:
        st.metric("Estimasi Total PO", f"Rp {po_val:,.0f}",
                  help="Nilai pembelian yang diperlukan")
    with k5:
        st.metric("✅ Aman", f"{aman:,}",
                  help="Stok cukup untuk 2 minggu+")

    st.markdown("---")

    # Charts row
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Distribusi Status Stok")
        status_counts = df[df['status']!='⚫ NO SALES']['status'].value_counts().reset_index()
        status_counts.columns = ['Status','Jumlah']
        colors_map = {
            '🔴 BELI HARI INI':'#B71C1C',
            '🟠 BELI MINGGU INI':'#E65100',
            '🟡 PANTAU':'#F9A825',
            '✅ AMAN':'#2E7D32',
        }
        fig = px.pie(status_counts, values='Jumlah', names='Status',
                     color='Status',
                     color_discrete_map=colors_map,
                     hole=0.4)
        fig.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                          legend=dict(orientation='h', y=-0.1))
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("Distribusi Klasifikasi SKU")
        klas_counts = df['klasifikasi'].value_counts().reset_index()
        klas_counts.columns = ['Klasifikasi','Jumlah']
        klas_colors = {
            'FAST MOVING':'#E91E63',
            'MEDIUM MOVING':'#E65100',
            'SLOW MOVING':'#546E7A',
            'NO SALES':'#9E9E9E',
        }
        fig2 = px.bar(klas_counts, x='Klasifikasi', y='Jumlah',
                      color='Klasifikasi',
                      color_discrete_map=klas_colors,
                      text='Jumlah')
        fig2.update_traces(textposition='outside')
        fig2.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                           showlegend=False, xaxis_title='', yaxis_title='Jumlah SKU')
        st.plotly_chart(fig2, use_container_width=True)

    # Top brands
    st.subheader("Top 15 Brand — Nilai PO Dibutuhkan")
    brand_po = df[df['perlu_beli']>0].groupby('brand').agg(
        sku_count=('sku','count'),
        po_value=('po_value','sum'),
        perlu_beli_total=('perlu_beli','sum'),
    ).reset_index().sort_values('po_value', ascending=False).head(15)

    if len(brand_po) > 0:
        fig3 = px.bar(brand_po, x='brand', y='po_value',
                      color='po_value',
                      color_continuous_scale=['#FCE4EC','#E91E63','#B71C1C'],
                      text=brand_po['po_value'].apply(lambda x: f"Rp {x:,.0f}"),
                      hover_data={'sku_count':True,'perlu_beli_total':True})
        fig3.update_traces(textposition='outside', textfont_size=9)
        fig3.update_layout(height=320, margin=dict(t=10,b=60,l=0,r=0),
                           xaxis_title='', yaxis_title='Nilai PO (Rp)',
                           coloraxis_showscale=False,
                           xaxis_tickangle=-30)
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.success("Semua stok aman! Tidak ada yang perlu dipesan.")

    # Days to stockout scatter
    st.subheader("SKU Fast Moving — Hari Sampai Stockout vs Stok Saat Ini")
    fast_df = df[(df['klasifikasi']=='FAST MOVING') & (df['avg_daily']>0)].head(80)
    if len(fast_df) > 0:
        fig4 = px.scatter(fast_df,
                          x='stock_effective', y='days_to_out',
                          color='status',
                          color_discrete_map={'🔴 BELI HARI INI':'#B71C1C',
                                              '🟠 BELI MINGGU INI':'#E65100',
                                              '🟡 PANTAU':'#F9A825',
                                              '✅ AMAN':'#2E7D32'},
                          hover_data={'brand':True,'product_full':True,
                                      'avg_daily':True,'perlu_beli':True},
                          size='avg_daily', size_max=20,
                          labels={'stock_effective':'Stok Saat Ini',
                                  'days_to_out':'Hari sampai Kosong'})
        fig4.add_hline(y=7, line_dash='dash', line_color='#E65100',
                       annotation_text='7 hari', annotation_position='right')
        fig4.add_hline(y=3, line_dash='dash', line_color='#B71C1C',
                       annotation_text='Lead time', annotation_position='right')
        fig4.update_layout(height=300, margin=dict(t=10,b=10,l=0,r=0))
        st.plotly_chart(fig4, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════
# PAGE: PURCHASE PLANNER
# ═══════════════════════════════════════════════════════════════════
elif page == "🚨 Purchase Planner":
    st.title("🚨 Purchase Planner")
    st.caption("Daftar SKU yang perlu dibeli. Update data di tab 📥 Import Data untuk akurasi lebih baik.")

    # Filter controls
    cf1, cf2, cf3, cf4 = st.columns([2, 2, 2, 2])
    with cf1:
        status_filter = st.multiselect(
            "Filter Status",
            ['🔴 BELI HARI INI','🟠 BELI MINGGU INI','🟡 PANTAU','✅ AMAN','⚫ NO SALES'],
            default=['🔴 BELI HARI INI','🟠 BELI MINGGU INI'],
        )
    with cf2:
        brand_opts = ['Semua'] + sorted(df['brand'].dropna().unique().tolist())
        brand_filter = st.selectbox("Filter Brand", brand_opts)
    with cf3:
        klas_opts = ['Semua','FAST MOVING','MEDIUM MOVING','SLOW MOVING']
        klas_filter = st.selectbox("Filter Klasifikasi", klas_opts)
    with cf4:
        min_beli = st.number_input("Min Qty Beli", min_value=0, value=1, step=1)

    # Apply filters
    filtered = df.copy()
    if status_filter:
        filtered = filtered[filtered['status'].isin(status_filter)]
    if brand_filter != 'Semua':
        filtered = filtered[filtered['brand'] == brand_filter]
    if klas_filter != 'Semua':
        filtered = filtered[filtered['klasifikasi'] == klas_filter]
    if min_beli > 0:
        filtered = filtered[filtered['perlu_beli'] >= min_beli]

    filtered = filtered.sort_values(['days_to_out', 'perlu_beli'], ascending=[True, False])

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("SKU ditampilkan", len(filtered))
    m2.metric("Total unit dibeli", f"{filtered['perlu_beli'].sum():,}")
    m3.metric("Estimasi PO", f"Rp {filtered['po_value'].sum():,.0f}")
    m4.metric("Brand terdampak", filtered['brand'].nunique())

    st.markdown("---")

    # Table
    display_cols = {
        'brand':           'Brand',
        'product_full':    'Nama Produk',
        'sku':             'SKU',
        'stock_effective': 'Stok Skrg',
        'avg_daily':       'Jual/Hari',
        'days_to_out':     'Hari s/d Kosong',
        'perlu_beli':      '🛒 PERLU BELI',
        'buy_price':       'Harga Beli',
        'po_value':        'Nilai PO (Rp)',
        'klasifikasi':     'Klasifikasi',
        'status':          'STATUS',
    }

    show_df = filtered[list(display_cols.keys())].rename(columns=display_cols).copy()
    show_df['Hari s/d Kosong'] = show_df['Hari s/d Kosong'].apply(
        lambda x: '∞' if x >= 999 else f'{x:.1f}')

    def color_status_row(row):
        s = row.get('STATUS', '')
        c_map = {
            '🔴 BELI HARI INI':   'background-color: #FFEBEE',
            '🟠 BELI MINGGU INI': 'background-color: #FFF3E0',
            '🟡 PANTAU':          'background-color: #FFFDE7',
            '✅ AMAN':            'background-color: #E8F5E9',
        }
        bg = c_map.get(s, '')
        return [bg] * len(row)

    styled = show_df.style.apply(color_status_row, axis=1).format({
        'Stok Skrg': '{:.0f}',
        'Jual/Hari': '{:.2f}',
        '🛒 PERLU BELI': '{:.0f}',
        'Harga Beli': 'Rp {:,.0f}',
        'Nilai PO (Rp)': 'Rp {:,.0f}',
    })

    st.dataframe(styled, use_container_width=True, height=480,
                 column_config={
                     '🛒 PERLU BELI': st.column_config.NumberColumn(
                         '🛒 PERLU BELI', format='%d unit', width='small'),
                     'STATUS': st.column_config.TextColumn('STATUS', width='medium'),
                 })

    # Download
    dl1, dl2 = st.columns(2)
    with dl1:
        po_export = filtered[['brand','product_full','sku','stock_effective',
                               'avg_daily','days_to_out','perlu_beli',
                               'buy_price','po_value','status']].copy()
        po_export.columns = ['Brand','Nama Produk','SKU','Stok Saat Ini',
                              'Jual/Hari','Hari s/d Kosong','PERLU BELI',
                              'Harga Beli','Nilai PO','Status']
        st.download_button(
            "⬇️ Download Purchase Order (Excel)",
            data=to_excel_bytes(po_export, 'Purchase Order'),
            file_name=f"PO_Merona_{date.today().strftime('%Y%m%d')}.xlsx",
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    with dl2:
        st.download_button(
            "⬇️ Download CSV",
            data=filtered.to_csv(index=False).encode('utf-8-sig'),
            file_name=f"purchase_planner_{date.today().strftime('%Y%m%d')}.csv",
            mime='text/csv',
        )

# ═══════════════════════════════════════════════════════════════════
# PAGE: SKU CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════
elif page == "🏆 SKU Classification":
    st.title("🏆 SKU Classification")

    cf1, cf2, cf3, cf4 = st.columns(4)
    with cf1:
        brand_f = st.selectbox("Brand", ['Semua'] + sorted(df['brand'].unique().tolist()))
    with cf2:
        klas_f  = st.selectbox("Klasifikasi", ['Semua'] + df['klasifikasi'].unique().tolist())
    with cf3:
        status_f= st.selectbox("Status", ['Semua'] + df['status'].unique().tolist())
    with cf4:
        search  = st.text_input("Cari Produk / SKU", "")

    filtered = df.copy()
    if brand_f  != 'Semua': filtered = filtered[filtered['brand']==brand_f]
    if klas_f   != 'Semua': filtered = filtered[filtered['klasifikasi']==klas_f]
    if status_f != 'Semua': filtered = filtered[filtered['status']==status_f]
    if search:
        s = search.lower()
        filtered = filtered[
            filtered['product_full'].str.lower().str.contains(s, na=False) |
            filtered['sku'].str.lower().str.contains(s, na=False) |
            filtered['brand'].str.lower().str.contains(s, na=False)
        ]
    filtered = filtered.sort_values('avg_daily', ascending=False)

    st.caption(f"Menampilkan {len(filtered):,} dari {len(df):,} SKU")

    show = filtered[['brand','product_full','sku','stock_effective',
                      'avg_daily','klasifikasi','margin_pct',
                      'buy_price','sell_price','perlu_beli','status']].copy()
    show.columns = ['Brand','Nama Produk','SKU','Stok','Jual/Hari',
                    'Klasifikasi','Margin%','Harga Beli','Harga Jual',
                    'Perlu Beli','Status']

    st.dataframe(show, use_container_width=True, height=540,
                 column_config={
                     'Margin%': st.column_config.NumberColumn('Margin%', format='%.1f%%'),
                     'Harga Beli': st.column_config.NumberColumn('Harga Beli', format='Rp %d'),
                     'Harga Jual': st.column_config.NumberColumn('Harga Jual', format='Rp %d'),
                     'Perlu Beli': st.column_config.NumberColumn('Perlu Beli', format='%d unit'),
                 })

    st.download_button(
        "⬇️ Download Excel",
        data=to_excel_bytes(show, 'SKU Classification'),
        file_name=f"SKU_Classification_{date.today().strftime('%Y%m%d')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

# ═══════════════════════════════════════════════════════════════════
# PAGE: IMPORT DATA
# ═══════════════════════════════════════════════════════════════════
elif page == "📥 Import Data":
    st.title("📥 Import Data")
    st.info("Upload laporan dari POS kamu. Sistem auto-detect kolom SKU dan Qty. "
            "Bisa upload dari 1 toko, 3 toko sekaligus, atau campuran — sistem akan SUMIF otomatis.")

    tab1, tab2 = st.tabs(["📊 Upload Penjualan", "📦 Upload Stok"])

    with tab1:
        st.subheader("Upload Data Penjualan")
        st.markdown("""
        **Cara export dari POS:**
        1. Buka POS → Laporan → **Item Penjualan Berdasarkan Brand**
        2. Set periode yang diinginkan
        3. Export ke Excel
        4. Upload di sini (boleh upload 3 file sekaligus dari 3 toko)
        """)

        period_input = st.number_input(
            "Periode data (berapa hari)?",
            min_value=1, max_value=90,
            value=st.session_state.settings.get('period_days', 21),
            help="Jumlah hari dari data yang kamu upload. Dipakai untuk hitung rata-rata per hari."
        )

        sales_files = st.file_uploader(
            "Upload file penjualan (Excel/CSV)",
            accept_multiple_files=True,
            type=['xlsx','xls','csv'],
            key='sales_upload'
        )

        if sales_files:
            all_sales = []
            errors = []
            for f in sales_files:
                df_raw, err = parse_uploaded(f, [])
                if err:
                    errors.append(f"{f.name}: {err}")
                    continue
                agg, err2 = process_sales(df_raw)
                if err2:
                    errors.append(f"{f.name}: {err2}")
                    continue
                all_sales.append(agg)
                st.success(f"✓ {f.name} — {len(df_raw):,} baris, {agg['sku'].nunique():,} SKU unik")

            if errors:
                for e in errors:
                    st.error(e)

            if all_sales:
                combined = pd.concat(all_sales).groupby('sku')['qty'].sum().reset_index()
                st.info(f"Total: {combined['sku'].nunique():,} SKU unik · {combined['qty'].sum():,} unit terjual")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Simpan Data Penjualan", type='primary'):
                        st.session_state.sales_data = combined
                        st.session_state.settings['period_days'] = period_input
                        st.session_state.last_import = datetime.now().strftime('%d/%m/%Y %H:%M')
                        st.success("Data penjualan tersimpan! Buka Purchase Planner untuk melihat update.")
                        st.balloons()
                with col2:
                    if st.session_state.sales_data is not None:
                        if st.button("🗑 Reset ke Baseline"):
                            st.session_state.sales_data = None
                            st.warning("Data penjualan direset ke baseline Mei 2026")

    with tab2:
        st.subheader("Upload Data Stok Terkini")
        st.markdown("""
        **Cara export dari POS:**
        1. Buka POS → Laporan → **Sisa Stok Produk**
        2. Set tanggal hari ini
        3. Export ke Excel
        4. Upload di sini (boleh upload dari 3 toko sekaligus)
        """)

        stock_files = st.file_uploader(
            "Upload file stok (Excel/CSV)",
            accept_multiple_files=True,
            type=['xlsx','xls','csv'],
            key='stock_upload'
        )

        if stock_files:
            all_stocks = []
            for f in stock_files:
                df_raw, err = parse_uploaded(f, [])
                if err:
                    st.error(f"{f.name}: {err}")
                    continue
                agg, err2 = process_stock(df_raw)
                if err2:
                    st.error(f"{f.name}: {err2}")
                    continue
                all_stocks.append(agg)
                total_stock = agg['stock'].sum()
                st.success(f"✓ {f.name} — {agg['sku'].nunique():,} SKU · Total {total_stock:,.0f} unit")

            if all_stocks:
                combined_stock = pd.concat(all_stocks).groupby('sku')['stock'].sum().reset_index()
                st.info(f"Total: {combined_stock['sku'].nunique():,} SKU · {combined_stock['stock'].sum():,.0f} unit stok")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Simpan Data Stok", type='primary'):
                        st.session_state.stock_data = combined_stock
                        st.session_state.last_import = datetime.now().strftime('%d/%m/%Y %H:%M')
                        st.success("Data stok tersimpan!")
                        st.balloons()
                with col2:
                    if st.session_state.stock_data is not None:
                        if st.button("🗑 Reset Stok ke Baseline"):
                            st.session_state.stock_data = None
                            st.warning("Stok direset ke baseline 22 Mei 2026")

        # Show current stock summary
        if st.session_state.stock_data is not None:
            st.markdown("---")
            st.subheader("Stok yang sedang aktif")
            st.dataframe(
                st.session_state.stock_data.sort_values('stock', ascending=False).head(20),
                use_container_width=True, height=300
            )

# ═══════════════════════════════════════════════════════════════════
# PAGE: DEADSTOCK
# ═══════════════════════════════════════════════════════════════════
elif page == "💤 Deadstock":
    st.title("💤 Deadstock — Modal Terikat")

    deadstock = df[(df['avg_daily'] == 0) & (df['stock_effective'] > 0)].copy()
    deadstock['nilai_modal'] = deadstock['stock_effective'] * deadstock['buy_price']
    deadstock = deadstock.sort_values('nilai_modal', ascending=False)

    total_modal = deadstock['nilai_modal'].sum()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total SKU Deadstock", f"{len(deadstock):,}")
    m2.metric("Total Modal Terikat", f"Rp {total_modal:,.0f}")
    m3.metric("Rata-rata per SKU", f"Rp {total_modal/max(len(deadstock),1):,.0f}")

    st.warning(f"💡 Rp {total_modal:,.0f} modal terikat di barang tidak laku. "
               f"Pertimbangkan: promosi besar, bundle, retur ke supplier, atau hapus dari katalog.")

    st.markdown("---")

    cf1, cf2 = st.columns(2)
    with cf1:
        brand_dead = st.selectbox("Filter Brand",
                                   ['Semua'] + sorted(deadstock['brand'].unique().tolist()),
                                   key='dead_brand')
    with cf2:
        min_val = st.number_input("Min Nilai Modal (Rp)", value=0, step=10000)

    filtered_dead = deadstock.copy()
    if brand_dead != 'Semua': filtered_dead = filtered_dead[filtered_dead['brand']==brand_dead]
    if min_val > 0: filtered_dead = filtered_dead[filtered_dead['nilai_modal'] >= min_val]

    # Brand summary
    brand_dead_sum = filtered_dead.groupby('brand').agg(
        sku_count=('sku','count'),
        modal=('nilai_modal','sum')
    ).reset_index().sort_values('modal', ascending=False).head(20)

    if len(brand_dead_sum) > 0:
        fig = px.bar(brand_dead_sum, x='brand', y='modal',
                     text=brand_dead_sum['modal'].apply(lambda x: f"Rp {x:,.0f}"),
                     color='modal', color_continuous_scale=['#E8EAF6','#6A1B9A'],
                     title="Top Brand — Nilai Deadstock")
        fig.update_traces(textposition='outside', textfont_size=9)
        fig.update_layout(height=300, xaxis_tickangle=-30, coloraxis_showscale=False,
                          xaxis_title='', margin=dict(t=40,b=60))
        st.plotly_chart(fig, use_container_width=True)

    show_dead = filtered_dead[['brand','product_full','sku',
                                'stock_effective','buy_price','nilai_modal']].copy()
    show_dead.columns = ['Brand','Nama Produk','SKU','Stok','Harga Beli','Nilai Modal']
    st.dataframe(show_dead, use_container_width=True, height=400,
                 column_config={
                     'Harga Beli': st.column_config.NumberColumn('Harga Beli', format='Rp %d'),
                     'Nilai Modal': st.column_config.NumberColumn('Nilai Modal', format='Rp %d'),
                 })

    st.download_button("⬇️ Download Daftar Deadstock",
                       data=to_excel_bytes(show_dead, 'Deadstock'),
                       file_name=f"Deadstock_{date.today().strftime('%Y%m%d')}.xlsx",
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ═══════════════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ═══════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("⚙️ Settings")
    st.info("Ubah parameter sistem. Semua halaman akan otomatis menyesuaikan.")

    with st.form("settings_form"):
        st.subheader("Parameter Restock")
        c1, c2 = st.columns(2)
        with c1:
            period = st.number_input("Periode Data (hari)",
                value=settings['period_days'], min_value=1, max_value=90,
                help="Default: 21 hari. Sesuaikan dengan periode data yang kamu upload.")
            lead   = st.number_input("Lead Time Supplier (hari)",
                value=settings['lead_time'], min_value=1, max_value=30,
                help="Berapa hari dari pesan ke barang datang? Default: 3")
            buf    = st.number_input("Buffer Stok (hari)",
                value=settings['buffer_days'], min_value=0, max_value=60,
                help="Safety stock dalam hari. Default: 7 hari")
        with c2:
            horizon= st.number_input("Horizon Perencanaan (hari)",
                value=settings['horizon'], min_value=1, max_value=30,
                help="Perencanaan untuk berapa hari ke depan? Default: 7 hari")
            fast_t = st.number_input("Threshold Fast Moving (unit/bulan)",
                value=settings['fast_thr'], min_value=1,
                help="SKU yang jual ≥ ini/bulan = Fast Moving")
            med_t  = st.number_input("Threshold Medium Moving (unit/bulan)",
                value=settings['med_thr'], min_value=1,
                help="SKU yang jual antara Medium-Fast = Medium Moving")

        submitted = st.form_submit_button("💾 Simpan Settings", type='primary')
        if submitted:
            st.session_state.settings = {
                'period_days': period, 'lead_time': lead,
                'buffer_days': buf,   'horizon': horizon,
                'fast_thr': fast_t,   'med_thr': med_t,
            }
            st.success("Settings tersimpan!")
            st.rerun()

    st.markdown("---")
    st.subheader("Reset Data")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("🗑 Reset Sales ke Baseline"):
            st.session_state.sales_data = None
            st.success("Reset!")
    with c2:
        if st.button("🗑 Reset Stok ke Baseline"):
            st.session_state.stock_data = None
            st.success("Reset!")
    with c3:
        if st.button("🗑 Reset Semua Settings"):
            st.session_state.settings = {}
            st.success("Reset ke default!")

    st.markdown("---")
    st.subheader("Info Sistem")
    st.markdown(f"""
    - **Baseline data:** Penjualan 1-21 Mei 2026, Stok 22 Mei 2026
    - **Total SKU dalam katalog:** {len(baseline):,}
    - **Versi app:** 1.0.0
    """)
