"""
MERONA PLATFORM — Unified Web App
Flask + SQLite + Pandas
Run  : gunicorn app:app  (production)
       python app.py     (development)
"""

import os, json, gzip, io, csv, time, threading, sqlite3
from datetime import datetime, date
from functools import wraps

import pandas as pd
import numpy as np
import requests
from flask import (Flask, render_template_string, request, jsonify,
                   session, redirect, url_for, send_file, flash)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'merona-dev-secret-2024-change-in-prod')

# ── ENV CONFIG ────────────────────────────────────────────────────────
ADMIN_USER     = os.environ.get('ADMIN_USER',     'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'merona2024')
DB_PATH        = os.environ.get('DB_PATH',        'merona.db')

# ── DATABASE ──────────────────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS app_data (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS blast_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        total INTEGER,
        sent INTEGER,
        failed INTEGER,
        skipped INTEGER,
        segments TEXT,
        notes TEXT
    );
    """)
    db.commit()
    db.close()

def db_save(key, value):
    db = get_db()
    db.execute("""INSERT INTO app_data(key,value,updated_at) VALUES(?,?,?)
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                  updated_at=excluded.updated_at""",
               (key, json.dumps(value, ensure_ascii=False),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    db.commit(); db.close()

def db_load(key, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM app_data WHERE key=?", (key,)).fetchone()
    db.close()
    if row:
        try: return json.loads(row['value'])
        except: return default
    return default

def db_ts(key):
    db = get_db()
    row = db.execute("SELECT updated_at FROM app_data WHERE key=?", (key,)).fetchone()
    db.close()
    return row['updated_at'] if row else None

# ── BASELINE DATA ─────────────────────────────────────────────────────
_baseline_df = None

def load_baseline():
    global _baseline_df
    if _baseline_df is not None:
        return _baseline_df
    gz = os.path.join(os.path.dirname(__file__), 'baseline_data.gz')
    if os.path.exists(gz):
        with gzip.open(gz, 'rb') as f:
            data = json.loads(f.read().decode())
        _baseline_df = pd.DataFrame(data)
        _baseline_df['sku'] = _baseline_df['sku'].astype(str)
    else:
        _baseline_df = pd.DataFrame()
    return _baseline_df

# ── SETTINGS ──────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    'period_days': 21, 'lead_time': 3, 'buffer_days': 7,
    'horizon': 7, 'fast_thr': 20, 'med_thr': 5, 'margin_thr': 25,
    'fonnte_token': '', 'blast_delay': 4,
}

def get_settings():
    saved = db_load('settings', {})
    return {**DEFAULT_SETTINGS, **saved}

# ── PRIORITY CLASS ────────────────────────────────────────────────────
PORDER = {'⭐ STARS':1,'📦 VOLUME':2,'💰 CASH COW':3,
          '🔵 STANDARD':4,'💎 HIDDEN GEM':5,'⚠️ REVIEW':6,'💤 NO SALES':7}
PCOLOR = {'⭐ STARS':'#F59E0B','📦 VOLUME':'#3B82F6','💰 CASH COW':'#10B981',
          '🔵 STANDARD':'#6B7280','💎 HIDDEN GEM':'#8B5CF6','⚠️ REVIEW':'#EF4444',
          '💤 NO SALES':'#9CA3AF'}
PBG    = {'⭐ STARS':'#FFFBEB','📦 VOLUME':'#EFF6FF','💰 CASH COW':'#ECFDF5',
          '🔵 STANDARD':'#F9FAFB','💎 HIDDEN GEM':'#F5F3FF','⚠️ REVIEW':'#FEF2F2',
          '💤 NO SALES':'#F9FAFB'}

def compute(baseline, sales_df, stock_df, S):
    df = baseline.copy()
    if sales_df is not None and len(sales_df):
        sales_df = sales_df.copy(); sales_df['sku'] = sales_df['sku'].astype(str)
        df = df.merge(sales_df.rename(columns={'qty':'qty_new'}), on='sku', how='left')
        df['qty_eff'] = np.where(df['qty_new'].notna()&(df['qty_new']>0), df['qty_new'], df['qty_total'])
        df['period']  = S['period_days']
    else:
        df['qty_eff'] = df['qty_total']; df['period'] = 21
    if stock_df is not None and len(stock_df):
        stock_df = stock_df.copy(); stock_df['sku'] = stock_df['sku'].astype(str)
        df = df.merge(stock_df.rename(columns={'stock':'stock_new'}), on='sku', how='left')
        df['stock_eff'] = np.where(df['stock_new'].notna()&(df['stock_new']>=0), df['stock_new'], df['stock_total'])
    else:
        df['stock_eff'] = df['stock_total']
    df['avg_daily']   = (df['qty_eff'] / df['period']).round(3)
    df['monthly_est'] = df['avg_daily'] * 30
    def pc(row):
        m = row['monthly_est']; mg = row.get('margin_pct', 0)
        if m < 0.1: return '💤 NO SALES'
        hi = mg >= S['margin_thr']
        if   m >= S['fast_thr'] and hi: return '⭐ STARS'
        elif m >= S['fast_thr']:         return '📦 VOLUME'
        elif m >= S['med_thr']  and hi: return '💰 CASH COW'
        elif m >= S['med_thr']:          return '🔵 STANDARD'
        elif hi:                         return '💎 HIDDEN GEM'
        else:                            return '⚠️ REVIEW'
    df['priority_class'] = df.apply(pc, axis=1)
    df['priority_num']   = df['priority_class'].map(PORDER).fillna(9)
    df['days_to_out']    = np.where(df['avg_daily']>0, (df['stock_eff']/df['avg_daily']).round(1), 999)
    df['perlu_beli']     = np.maximum(0, np.ceil(
        df['avg_daily']*(S['horizon']+S['buffer_days']+S['lead_time'])-df['stock_eff'])).astype(int)
    df['po_value']       = df['perlu_beli'] * df['buy_price']
    def st(row):
        if row['avg_daily'] == 0: return '⚫ NO SALES'
        d = row['days_to_out']
        if d < S['lead_time']:                    return '🔴 BELI HARI INI'
        if d < S['horizon'] + S['lead_time']:     return '🟠 BELI MINGGU INI'
        if d < (S['horizon']+S['buffer_days'])*2: return '🟡 PANTAU'
        return '✅ AMAN'
    df['status']   = df.apply(st, axis=1)
    df['sort_key'] = df['priority_num'] * 1000 + df['days_to_out'].clip(upper=999)
    return df

def get_main_df():
    baseline  = load_baseline()
    if baseline.empty: return pd.DataFrame()
    S         = get_settings()
    sales_raw = db_load('sales_data')
    stock_raw = db_load('stock_data')
    sales_df  = pd.DataFrame(sales_raw) if sales_raw else None
    stock_df  = pd.DataFrame(stock_raw) if stock_raw else None
    return compute(baseline, sales_df, stock_df, S)

def find_col(df, aliases):
    for a in aliases:
        if a in df.columns: return a
    return None

def parse_sales(file_bytes, filename):
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig')
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))
        df.columns = [str(c).strip().lower().replace(' ','_') for c in df.columns]
        sku = find_col(df, ['item_sku','sku','item_code','kode_produk','barcode'])
        qty = find_col(df, ['qty','quantity','qty_terjual','jumlah','terjual'])
        if not sku or not qty:
            return None, f"Kolom SKU/Qty tidak ditemukan. Kolom: {list(df.columns)}"
        df[qty] = pd.to_numeric(df[qty], errors='coerce').fillna(0)
        agg = df[df[qty]>0].groupby(sku)[qty].sum().reset_index()
        agg.columns = ['sku','qty']
        agg['sku'] = agg['sku'].astype(str)
        return agg.to_dict('records'), None
    except Exception as e:
        return None, str(e)

def parse_stock(file_bytes, filename):
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig')
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))
        df.columns = [str(c).strip().lower().replace(' ','_') for c in df.columns]
        sku = find_col(df, ['sku','item_sku','item_code','kode_produk','barcode'])
        stk = find_col(df, ['stock','stok','qty','quantity','sisa_stok','sisa','jumlah'])
        if not sku or not stk:
            return None, f"Kolom SKU/Stock tidak ditemukan. Kolom: {list(df.columns)}"
        df[stk] = pd.to_numeric(df[stk], errors='coerce').fillna(0)
        agg = df.groupby(sku)[stk].sum().reset_index()
        agg.columns = ['sku','stock']
        agg['sku'] = agg['sku'].astype(str)
        return agg.to_dict('records'), None
    except Exception as e:
        return None, str(e)

# ── WA BLAST ──────────────────────────────────────────────────────────
blast_state = {
    'running': False, 'log': [], 'total': 0,
    'sent': 0, 'failed': 0, 'skipped': 0, 'idx': 0
}

DEFAULT_TEMPLATES = {
    'Champions': 'Halo Kak {nama}! 👑\n\nKamu adalah Champions Member Merona yang luar biasa!\n\nAda sesuatu spesial menunggu kamu di toko 💕\n\n— Tim Merona',
    'Loyal Customers': 'Halo Kak {nama}! 🌸\n\nDouble Point tiap Selasa & Kamis bulan ini!\n\nYuk makin cantik bareng kami!\n\n— Tim Merona',
    "Can't Lose Them": 'Halo Kak {nama}, kami kangen kamu! 😢\n\nVoucher spesial Rp 50.000 sudah menunggu. Berlaku 7 hari!\n\n— Tim Merona',
    'At Risk': 'Kak {nama}, sudah lama nih! 😊\n\nVoucher Rp 30.000 kalau balik minggu ini!\n\n— Tim Merona',
    'Inactive / Belum Transaksi': 'Halo Kak {nama}! 👋\n\nVoucher first-purchase 15rb menunggu kamu! Berlaku 30 hari.\n\n— Tim Merona',
}

def fmt_phone(p):
    p = str(p).replace(' ','').replace('-','').replace('(','').replace(')','').replace('+','')
    if p.startswith('62'): return p
    if p.startswith('08'): return '62'+p[1:]
    if p.startswith('8'):  return '62'+p
    return p

def send_wa(phone, msg, token):
    try:
        r = requests.post('https://api.fonnte.com/send',
            headers={'Authorization': token},
            data={'target': phone, 'message': msg, 'countryCode': '62'},
            timeout=15)
        if r.status_code == 200:
            d = {}
            try: d = r.json()
            except: pass
            if d.get('status') is False:
                return False, d.get('reason', 'Ditolak Fonnte')
            return True, 'OK'
        return False, f'HTTP {r.status_code}'
    except requests.exceptions.ConnectionError: return False, 'Koneksi gagal'
    except requests.exceptions.Timeout:         return False, 'Timeout'
    except Exception as e:                       return False, str(e)

def run_blast(customers, templates, token, delay, active_segs):
    global blast_state
    blast_state.update({'running':True,'log':[],'sent':0,'failed':0,'skipped':0,
                        'idx':0,'total':len(customers)})
    def log(msg, typ='info'):
        blast_state['log'].append({
            't': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'type': typ})
    log(f'Mulai blast — {len(customers)} customer, segmen: {", ".join(active_segs)}')
    for i, c in enumerate(customers):
        blast_state['idx'] = i+1
        seg  = c.get('segment','')
        nama = c.get('nama','Customer')
        phone = fmt_phone(c.get('nomor_hp',''))
        if seg not in active_segs:
            blast_state['skipped'] += 1
            log(f'[{i+1}] SKIP (segmen): {nama}', 'skip'); continue
        if not phone or len(phone) < 8:
            blast_state['skipped'] += 1
            log(f'[{i+1}] SKIP (nomor): {nama}', 'skip'); continue
        tpl = templates.get(seg, 'Halo Kak {nama}! Ada promo dari Merona 😊\n— Tim Merona')
        msg = tpl.replace('{nama}', nama).replace('{segment}', seg)
        ok, reason = send_wa(phone, msg, token)
        if ok:
            blast_state['sent'] += 1
            log(f'[{i+1}] OK — {nama} ({phone})', 'ok')
        else:
            blast_state['failed'] += 1
            log(f'[{i+1}] GAGAL — {nama}: {reason}', 'err')
        if i < len(customers)-1:
            time.sleep(delay)
    blast_state['running'] = False
    log(f'SELESAI — OK:{blast_state["sent"]} Gagal:{blast_state["failed"]} Skip:{blast_state["skipped"]}')
    # Save history
    db = get_db()
    segs = json.dumps(list(active_segs))
    db.execute("INSERT INTO blast_history(created_at,total,sent,failed,skipped,segments) VALUES(?,?,?,?,?,?)",
               (datetime.now().strftime('%Y-%m-%d %H:%M'), len(customers),
                blast_state['sent'], blast_state['failed'], blast_state['skipped'], segs))
    db.commit(); db.close()

# ── AUTH ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── HTML TEMPLATES ────────────────────────────────────────────────────
BASE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} — Merona Platform</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
<style>
:root{--pink:#E91E63;--pink2:#C2185B;--sidebar:#1a1a2e;}
body{background:#f4f6f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.sidebar{width:230px;min-height:100vh;background:var(--sidebar);position:fixed;top:0;left:0;z-index:100;padding-top:0}
.sidebar-brand{padding:20px 20px 15px;border-bottom:1px solid rgba(255,255,255,.1)}
.sidebar-brand .name{color:#fff;font-size:16px;font-weight:600;margin:0}
.sidebar-brand .sub{color:rgba(255,255,255,.5);font-size:11px}
.sidebar-menu{padding:10px 0}
.sidebar-menu a{display:flex;align-items:center;gap:10px;padding:10px 20px;color:rgba(255,255,255,.7);text-decoration:none;font-size:13px;transition:all .15s}
.sidebar-menu a:hover,.sidebar-menu a.active{background:rgba(255,255,255,.1);color:#fff}
.sidebar-menu a.active{border-left:3px solid var(--pink)}
.sidebar-menu .icon{font-size:16px;width:20px;text-align:center}
.sidebar-menu .section{padding:15px 20px 5px;font-size:10px;color:rgba(255,255,255,.3);text-transform:uppercase;letter-spacing:1px}
.main{margin-left:230px;padding:24px}
.topbar{background:#fff;border-radius:10px;padding:14px 20px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.page-title{font-size:20px;font-weight:600;margin:0}
.card{border:none;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.kpi-card{padding:18px;border-left:4px solid var(--pink)}
.kpi-val{font-size:26px;font-weight:700;color:var(--pink)}
.kpi-lbl{font-size:12px;color:#888;margin-top:2px}
.btn-pink{background:var(--pink);border-color:var(--pink);color:#fff}
.btn-pink:hover{background:var(--pink2);border-color:var(--pink2);color:#fff}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
.badge-priority{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;color:#fff;font-weight:500}
.log-box{background:#1e1e1e;border-radius:8px;padding:12px;height:220px;overflow-y:auto;font-family:monospace;font-size:11px;line-height:1.7}
.log-ok{color:#a5d6a7}.log-err{color:#ef9a9a}.log-info{color:#90caf9}.log-skip{color:#757575}
.prog-bar-custom{height:8px;border-radius:4px;background:#e9ecef;overflow:hidden;margin:6px 0}
.prog-fill{height:100%;background:var(--pink);border-radius:4px;transition:width .3s}
.sidebar-status{padding:10px 20px;font-size:11px;color:rgba(255,255,255,.5);border-top:1px solid rgba(255,255,255,.08);margin-top:auto}
.sidebar-status .dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px}
.sidebar-footer{position:absolute;bottom:0;left:0;right:0}
table.dataTable td{font-size:12px}
.urgency-red{background:#fff5f5}
.urgency-orange{background:#fffbf0}
.urgency-yellow{background:#fffff0}
@media(max-width:768px){.sidebar{width:60px}.sidebar-brand .name,.sidebar-brand .sub,.sidebar-menu a span,.sidebar-menu .section{display:none}.sidebar-menu a{justify-content:center;padding:12px}.main{margin-left:60px}}
</style>
</head>
<body>
<div class="sidebar d-flex flex-column">
  <div class="sidebar-brand">
    <div class="name">💄 Merona</div>
    <div class="sub">Platform v1.0</div>
  </div>
  <div class="sidebar-menu flex-grow-1">
    <a href="/dashboard" class="{{ 'active' if active=='dashboard' else '' }}"><span class="icon">🏠</span><span>Dashboard</span></a>
    <div class="section">Operasional</div>
    <a href="/purchase" class="{{ 'active' if active=='purchase' else '' }}"><span class="icon">🚨</span><span>Purchase Planner</span></a>
    <a href="/sku" class="{{ 'active' if active=='sku' else '' }}"><span class="icon">🏆</span><span>SKU Matrix</span></a>
    <a href="/deadstock" class="{{ 'active' if active=='deadstock' else '' }}"><span class="icon">💤</span><span>Deadstock</span></a>
    <div class="section">CRM</div>
    <a href="/blast" class="{{ 'active' if active=='blast' else '' }}"><span class="icon">📱</span><span>WA Blast CRM</span></a>
    <a href="/customers" class="{{ 'active' if active=='customers' else '' }}"><span class="icon">👥</span><span>Customers</span></a>
    <div class="section">Data</div>
    <a href="/import" class="{{ 'active' if active=='import' else '' }}"><span class="icon">📥</span><span>Import Data</span></a>
    <a href="/settings" class="{{ 'active' if active=='settings' else '' }}"><span class="icon">⚙️</span><span>Settings</span></a>
  </div>
  <div class="sidebar-footer">
    <div class="sidebar-status">
      <div><span class="dot" style="background:{{ '#4CAF50' if db_status else '#f44336' }}"></span>{{ 'DB Terhubung' if db_status else 'DB Lokal' }}</div>
      <div style="margin-top:3px">{{ sales_ts or 'Baseline Mei 2026' }}</div>
    </div>
  </div>
</div>
<div class="main">
  <div class="topbar">
    <div class="page-title">{{ title }}</div>
    <div class="d-flex align-items-center gap-3">
      {% if flash_msg %}<div class="alert alert-{{ flash_type or 'info' }} py-1 px-3 mb-0 small">{{ flash_msg }}</div>{% endif %}
      <a href="/logout" class="btn btn-sm btn-outline-secondary">Logout</a>
    </div>
  </div>
  {% block content %}{% endblock %}
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.0/jquery.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/js/bootstrap.bundle.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
{% block scripts %}{% endblock %}
</body></html>"""

def render(template_str, **kwargs):
    import re
    # Extract content block
    cm = re.search(r'{%[-\s]*block content[-\s]*%}(.*?){%[-\s]*endblock[-\s]*%}', template_str, re.DOTALL)
    content = cm.group(1).strip() if cm else template_str
    # Extract scripts block
    sm = re.search(r'{%[-\s]*block scripts[-\s]*%}(.*?){%[-\s]*endblock[-\s]*%}', template_str, re.DOTALL)
    scripts = sm.group(1).strip() if sm else ''
    # Build final
    final = BASE.replace('{% block content %}{% endblock %}', content).replace('{% block scripts %}{% endblock %}', scripts)
    # Build sidebar defaults — only add if not already in kwargs (avoid duplicate keyword error)
    defaults = {'db_status': True, 'flash_msg': None, 'flash_type': None}
    if 'sales_ts' not in kwargs:
        _ts = db_ts('sales_data')
        defaults['sales_ts'] = ('Sales: ' + _ts[:10]) if _ts else None
    defaults.update(kwargs)
    return render_template_string(final, **defaults)

# ── ROUTES ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if session.get('logged_in') else url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        if (request.form.get('username') == ADMIN_USER and
                request.form.get('password') == ADMIN_PASSWORD):
            session['logged_in'] = True
            session.permanent = True
            return redirect(url_for('dashboard'))
        error = 'Username atau password salah'
    return render_template_string("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Login — Merona</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/css/bootstrap.min.css">
<style>body{background:#f4f6f9;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{border:none;border-radius:14px;box-shadow:0 4px 20px rgba(0,0,0,.08);width:100%;max-width:380px}
.brand{color:#E91E63;font-size:28px;font-weight:700;margin-bottom:4px}
.btn-pink{background:#E91E63;border-color:#E91E63;color:#fff}
.btn-pink:hover{background:#C2185B;border-color:#C2185B;color:#fff}</style></head>
<body><div class="card p-4 p-md-5">
  <div class="text-center mb-4">
    <div class="brand">💄 Merona</div>
    <div class="text-muted small">Platform Management</div>
  </div>
  {% if error %}<div class="alert alert-danger small">{{ error }}</div>{% endif %}
  <form method="post">
    <div class="mb-3"><label class="form-label small fw-500">Username</label>
      <input type="text" name="username" class="form-control" required autofocus></div>
    <div class="mb-4"><label class="form-label small fw-500">Password</label>
      <input type="password" name="password" class="form-control" required></div>
    <button class="btn btn-pink w-100">Masuk →</button>
  </form>
</div></body></html>""", error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── DASHBOARD ──────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    df = get_main_df()
    if df.empty:
        return render("{% block content %}<div class='alert alert-warning'>Data belum tersedia. <a href='/import'>Import data dulu</a>.</div>{% endblock %}", title='Dashboard', active='dashboard')
    urgent  = int((df['status']=='🔴 BELI HARI INI').sum())
    buy_wk  = int((df['status']=='🟠 BELI MINGGU INI').sum())
    po_val  = int(df['po_value'].sum())
    stars_n = int((df['priority_class']=='⭐ STARS').sum())
    total   = len(df)
    # Priority distribution
    pc_counts = df[df['priority_class']!='💤 NO SALES']['priority_class'].value_counts().to_dict()
    # Status distribution
    st_counts = {k:int(v) for k,v in df[df['status']!='⚫ NO SALES']['status'].value_counts().items()}
    # Top brands by PO
    top_brands = (df[df['perlu_beli']>0].groupby('brand')['po_value'].sum()
                  .nlargest(10).reset_index().to_dict('records'))
    # Recent history
    db = get_db()
    history = [dict(r) for r in db.execute(
        "SELECT * FROM blast_history ORDER BY id DESC LIMIT 5").fetchall()]
    db.close()
    return render(DASHBOARD_HTML, title='Dashboard', active='dashboard',
                  urgent=urgent, buy_wk=buy_wk, po_val=f'{po_val:,}',
                  stars_n=stars_n, total=total,
                  pc_counts=json.dumps(pc_counts),
                  st_counts=json.dumps(st_counts),
                  top_brands=json.dumps(top_brands),
                  history=history,
                  pc_colors=json.dumps(PCOLOR))

DASHBOARD_HTML = """{% block content %}
<div class="row g-3 mb-4">
  <div class="col-md-2"><div class="card kpi-card h-100" style="border-color:#3B82F6">
    <div class="kpi-val" style="color:#3B82F6">{{total}}</div><div class="kpi-lbl">Total SKU Aktif</div></div></div>
  <div class="col-md-2"><div class="card kpi-card h-100" style="border-color:#B71C1C">
    <div class="kpi-val" style="color:#B71C1C">{{urgent}}</div><div class="kpi-lbl">🔴 Beli Hari Ini</div></div></div>
  <div class="col-md-2"><div class="card kpi-card h-100" style="border-color:#E65100">
    <div class="kpi-val" style="color:#E65100">{{buy_wk}}</div><div class="kpi-lbl">🟠 Beli Minggu Ini</div></div></div>
  <div class="col-md-3"><div class="card kpi-card h-100" style="border-color:#2E7D32">
    <div class="kpi-val" style="color:#2E7D32;font-size:20px">Rp {{po_val}}</div><div class="kpi-lbl">Estimasi Total PO</div></div></div>
  <div class="col-md-3"><div class="card kpi-card h-100" style="border-color:#F59E0B">
    <div class="kpi-val" style="color:#F59E0B">{{stars_n}}</div><div class="kpi-lbl">⭐ STARS SKU</div></div></div>
</div>
{% if urgent > 0 %}
<div class="alert alert-danger d-flex align-items-center gap-2">
  <strong>⚠️ {{urgent}} SKU perlu dibeli HARI INI!</strong>
  <a href="/purchase" class="btn btn-sm btn-danger ms-2">Lihat Purchase Planner →</a>
</div>
{% endif %}
<div class="row g-3 mb-3">
  <div class="col-md-6"><div class="card p-3">
    <h6 class="fw-600 mb-3">Priority Matrix</h6>
    <canvas id="chartPriority" height="200"></canvas>
  </div></div>
  <div class="col-md-6"><div class="card p-3">
    <h6 class="fw-600 mb-3">Status Stok</h6>
    <canvas id="chartStatus" height="200"></canvas>
  </div></div>
</div>
<div class="row g-3">
  <div class="col-md-8"><div class="card p-3">
    <h6 class="fw-600 mb-3">Top 10 Brand — Nilai PO Dibutuhkan</h6>
    <canvas id="chartBrands" height="180"></canvas>
  </div></div>
  <div class="col-md-4"><div class="card p-3">
    <h6 class="fw-600 mb-3">Riwayat Blast Terakhir</h6>
    {% if history %}
    {% for h in history %}
    <div class="d-flex justify-content-between py-2 border-bottom small">
      <div><div class="fw-500">{{h.created_at}}</div><div class="text-muted">{{h.total}} customer</div></div>
      <div class="text-end"><span class="text-success fw-500">{{h.sent}} OK</span> / <span class="text-danger">{{h.failed}} gagal</span></div>
    </div>
    {% endfor %}
    {% else %}<div class="text-muted small">Belum ada riwayat blast</div>{% endif %}
    <a href="/blast" class="btn btn-sm btn-pink mt-3 w-100">Buat Blast Baru →</a>
  </div></div>
</div>
{% endblock %}
{% block scripts %}
<script>
const pcData = {{pc_counts|safe}};
const stData = {{st_counts|safe}};
const pcColors = {{pc_colors|safe}};
const topBrands = {{top_brands|safe}};
const stColors = {'🔴 BELI HARI INI':'#B71C1C','🟠 BELI MINGGU INI':'#E65100','🟡 PANTAU':'#F9A825','✅ AMAN':'#2E7D32'};
new Chart(document.getElementById('chartPriority'),{type:'bar',data:{
  labels:Object.keys(pcData),
  datasets:[{data:Object.values(pcData),backgroundColor:Object.keys(pcData).map(k=>pcColors[k]||'#ccc')}]
},options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}},responsive:true}});
new Chart(document.getElementById('chartStatus'),{type:'doughnut',data:{
  labels:Object.keys(stData),
  datasets:[{data:Object.values(stData),backgroundColor:Object.keys(stData).map(k=>stColors[k]||'#ccc')}]
},options:{responsive:true,cutout:'55%'}});
if(topBrands.length){
  new Chart(document.getElementById('chartBrands'),{type:'bar',data:{
    labels:topBrands.map(b=>b.brand),
    datasets:[{data:topBrands.map(b=>b.po_value),backgroundColor:'#E91E63'}]
  },options:{indexAxis:'y',plugins:{legend:{display:false}},responsive:true}});
}
</script>
{% endblock %}"""

# ── PURCHASE PLANNER ───────────────────────────────────────────────────
@app.route('/purchase')
@login_required
def purchase():
    df = get_main_df()
    if df.empty: return redirect('/import')
    status_filter    = request.args.get('status','urgent')
    brand_filter     = request.args.get('brand','')
    priority_filter  = request.args.get('priority','')
    filtered = df.copy()
    if status_filter == 'urgent':
        filtered = filtered[filtered['status'].isin(['🔴 BELI HARI INI','🟠 BELI MINGGU INI'])]
    elif status_filter == 'pantau':
        filtered = filtered[filtered['status']=='🟡 PANTAU']
    elif status_filter != 'all':
        filtered = filtered[filtered['status']==status_filter]
    if brand_filter:
        filtered = filtered[filtered['brand']==brand_filter]
    if priority_filter:
        filtered = filtered[filtered['priority_class']==priority_filter]
    filtered = filtered[filtered['perlu_beli']>0].sort_values('sort_key')
    brands = sorted(df['brand'].dropna().unique().tolist())
    priorities = sorted(df['priority_class'].unique().tolist())
    rows = []
    STATUS_COLOR = {'🔴 BELI HARI INI':'#FFEBEE','🟠 BELI MINGGU INI':'#FFF3E0',
                    '🟡 PANTAU':'#FFFDE7','✅ AMAN':'#E8F5E9'}
    for _, r in filtered.head(500).iterrows():
        pc = r['priority_class']
        rows.append({
            'priority': pc,
            'pc_color': PCOLOR.get(pc,'#ccc'),
            'pc_bg':    PBG.get(pc,'#fff'),
            'brand':    r['brand'],
            'product':  str(r['product_full'])[:60],
            'sku':      r['sku'],
            'stock':    int(r['stock_eff']),
            'avg':      round(r['avg_daily'],2),
            'days':     '∞' if r['days_to_out']>=999 else round(r['days_to_out'],1),
            'beli':     int(r['perlu_beli']),
            'harga':    f"Rp {int(r['buy_price']):,}",
            'nilai':    f"Rp {int(r['po_value']):,}",
            'status':   r['status'],
            'st_bg':    STATUS_COLOR.get(r['status'],'#fff'),
        })
    total_beli  = int(filtered['perlu_beli'].sum())
    total_nilai = int(filtered['po_value'].sum())
    total_sku   = len(filtered)
    return render(PURCHASE_HTML, title='Purchase Planner', active='purchase',
                  rows=rows, brands=brands, priorities=priorities,
                  status_filter=status_filter, brand_filter=brand_filter,
                  priority_filter=priority_filter,
                  total_beli=f'{total_beli:,}', total_nilai=f'{total_nilai:,}',
                  total_sku=total_sku)

PURCHASE_HTML = """{% block content %}
<div class="row g-2 mb-3 align-items-end">
  <div class="col-md-2">
    <label class="form-label small mb-1">Status Urgensi</label>
    <select class="form-select form-select-sm" id="fStatus" onchange="applyFilter()">
      <option value="urgent" {{'selected' if status_filter=='urgent' else ''}}>🔴🟠 Harus Beli</option>
      <option value="pantau" {{'selected' if status_filter=='pantau' else ''}}>🟡 Pantau</option>
      <option value="all"    {{'selected' if status_filter=='all' else ''}}>Semua</option>
    </select>
  </div>
  <div class="col-md-3">
    <label class="form-label small mb-1">Brand</label>
    <select class="form-select form-select-sm" id="fBrand" onchange="applyFilter()">
      <option value="">Semua Brand</option>
      {% for b in brands %}<option value="{{b}}" {{'selected' if brand_filter==b else ''}}>{{b}}</option>{% endfor %}
    </select>
  </div>
  <div class="col-md-3">
    <label class="form-label small mb-1">Priority Class</label>
    <select class="form-select form-select-sm" id="fPriority" onchange="applyFilter()">
      <option value="">Semua Priority</option>
      {% for p in priorities %}<option value="{{p}}" {{'selected' if priority_filter==p else ''}}>{{p}}</option>{% endfor %}
    </select>
  </div>
  <div class="col-md-4">
    <div class="d-flex gap-2 align-items-center">
      <div class="card p-2 text-center" style="min-width:80px"><div class="fw-700 text-danger">{{total_sku}}</div><div class="small text-muted">SKU</div></div>
      <div class="card p-2 text-center" style="min-width:100px"><div class="fw-700" style="color:#E65100">{{total_beli}}</div><div class="small text-muted">Unit beli</div></div>
      <div class="card p-2 text-center flex-grow-1"><div class="fw-700 text-success" style="font-size:13px">Rp {{total_nilai}}</div><div class="small text-muted">Estimasi PO</div></div>
    </div>
  </div>
</div>
<div class="card">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0" id="tblPurchase">
        <thead class="table-light"><tr>
          <th>Priority</th><th>Brand</th><th>Produk</th><th>SKU</th>
          <th>Stok</th><th>Jual/Hr</th><th>Hari s/d Kosong</th>
          <th>🛒 BELI</th><th>Harga Beli</th><th>Nilai PO</th><th>Status</th>
        </tr></thead>
        <tbody>
        {% for r in rows %}
        <tr style="background:{{r.pc_bg}}">
          <td><span class="badge-priority" style="background:{{r.pc_color}}">{{r.priority}}</span></td>
          <td class="fw-500">{{r.brand}}</td>
          <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{r.product}}">{{r.product}}</td>
          <td class="text-muted small">{{r.sku}}</td>
          <td class="text-center">{{r.stock}}</td>
          <td class="text-center">{{r.avg}}</td>
          <td class="text-center">{{r.days}}</td>
          <td class="text-center fw-700" style="color:#B71C1C;font-size:14px">{{r.beli}}</td>
          <td class="text-end">{{r.harga}}</td>
          <td class="text-end fw-600">{{r.nilai}}</td>
          <td><small style="background:{{r.st_bg}};padding:2px 6px;border-radius:8px">{{r.status}}</small></td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
<div class="mt-3 d-flex gap-2">
  <a href="/api/purchase/download" class="btn btn-sm btn-pink">⬇️ Download PO Excel</a>
  <a href="/api/purchase/csv" class="btn btn-sm btn-outline-secondary">⬇️ CSV</a>
</div>
{% endblock %}
{% block scripts %}
<script>
$('#tblPurchase').DataTable({pageLength:50,order:[[0,'asc']],language:{url:'//cdn.datatables.net/plug-ins/1.13.6/i18n/id.json'}});
function applyFilter(){
  var s=document.getElementById('fStatus').value;
  var b=encodeURIComponent(document.getElementById('fBrand').value);
  var p=encodeURIComponent(document.getElementById('fPriority').value);
  window.location='/purchase?status='+s+'&brand='+b+'&priority='+p;
}
</script>
{% endblock %}"""

# ── SKU MATRIX ─────────────────────────────────────────────────────────
@app.route('/sku')
@login_required
def sku():
    df = get_main_df()
    if df.empty: return redirect('/import')
    S = get_settings()
    brand_f    = request.args.get('brand','')
    priority_f = request.args.get('priority','')
    search_f   = request.args.get('q','').lower()
    filtered   = df.copy()
    if brand_f:    filtered = filtered[filtered['brand']==brand_f]
    if priority_f: filtered = filtered[filtered['priority_class']==priority_f]
    if search_f:
        filtered = filtered[
            filtered['product_full'].str.lower().str.contains(search_f,na=False)|
            filtered['sku'].str.lower().str.contains(search_f,na=False)|
            filtered['brand'].str.lower().str.contains(search_f,na=False)]
    filtered = filtered.sort_values('sort_key')
    rows = []
    for _, r in filtered.head(1000).iterrows():
        pc = r['priority_class']
        rows.append({
            'priority': pc, 'pc_color': PCOLOR.get(pc,'#ccc'), 'pc_bg': PBG.get(pc,'#fff'),
            'brand': r['brand'], 'product': str(r['product_full'])[:55], 'sku': r['sku'],
            'stock': int(r['stock_eff']), 'avg': round(r['avg_daily'],2),
            'monthly': round(r['monthly_est'],1), 'margin': round(r.get('margin_pct',0),1),
            'beli': int(r['perlu_beli']),
            'status': r['status'],
        })
    brands     = sorted(df['brand'].dropna().unique().tolist())
    priorities = sorted(df['priority_class'].unique().tolist())
    summary = {pc: int((df['priority_class']==pc).sum()) for pc in PORDER}
    return render(SKU_HTML, title='SKU Matrix', active='sku',
                  rows=rows, brands=brands, priorities=priorities,
                  brand_f=brand_f, priority_f=priority_f, q=search_f,
                  total_shown=len(filtered), total_all=len(df),
                  summary=summary, pc_colors=PCOLOR, margin_thr=S['margin_thr'])

SKU_HTML = """{% block content %}
<div class="row g-2 mb-3">
  {% for pc, n in summary.items() %}{% if pc != '💤 NO SALES' %}
  <div class="col"><div class="card p-2 text-center" style="border-top:3px solid {{pc_colors[pc]}}">
    <div class="fw-700" style="color:{{pc_colors[pc]}}">{{n}}</div>
    <div style="font-size:10px;color:#888">{{pc}}</div>
  </div></div>
  {% endif %}{% endfor %}
</div>
<div class="row g-2 mb-3 align-items-end">
  <div class="col-md-4"><input type="text" class="form-control form-control-sm" id="qSearch" placeholder="Cari produk / SKU / brand..." value="{{q}}" onchange="applyFilter()"></div>
  <div class="col-md-3"><select class="form-select form-select-sm" id="fBrand" onchange="applyFilter()">
    <option value="">Semua Brand</option>
    {% for b in brands %}<option value="{{b}}" {{'selected' if brand_f==b else ''}}>{{b}}</option>{% endfor %}
  </select></div>
  <div class="col-md-3"><select class="form-select form-select-sm" id="fPriority" onchange="applyFilter()">
    <option value="">Semua Priority</option>
    {% for p in priorities %}<option value="{{p}}" {{'selected' if priority_f==p else ''}}>{{p}}</option>{% endfor %}
  </select></div>
  <div class="col-md-2"><div class="text-muted small">{{total_shown}} / {{total_all}} SKU</div></div>
</div>
<div class="card">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0" id="tblSku">
        <thead class="table-light"><tr>
          <th>Priority</th><th>Brand</th><th>Produk</th><th>SKU</th>
          <th>Stok</th><th>Jual/Hr</th><th>Est/Bln</th><th>Margin%</th><th>Perlu Beli</th><th>Status</th>
        </tr></thead>
        <tbody>
        {% for r in rows %}
        <tr style="background:{{r.pc_bg}}">
          <td><span class="badge-priority" style="background:{{r.pc_color}}">{{r.priority}}</span></td>
          <td class="fw-500">{{r.brand}}</td>
          <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{r.product}}">{{r.product}}</td>
          <td class="text-muted small">{{r.sku}}</td>
          <td class="text-center">{{r.stock}}</td>
          <td class="text-center">{{r.avg}}</td>
          <td class="text-center">{{r.monthly}}</td>
          <td class="text-center">{{r.margin}}%</td>
          <td class="text-center {% if r.beli > 0 %}fw-700 text-danger{% endif %}">{{r.beli if r.beli > 0 else '—'}}</td>
          <td><small>{{r.status}}</small></td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
<div class="mt-2"><a href="/api/sku/download" class="btn btn-sm btn-pink">⬇️ Download Excel</a></div>
{% endblock %}
{% block scripts %}
<script>
$('#tblSku').DataTable({pageLength:50,order:[[0,'asc']],language:{url:'//cdn.datatables.net/plug-ins/1.13.6/i18n/id.json'}});
function applyFilter(){
  var q=encodeURIComponent(document.getElementById('qSearch').value);
  var b=encodeURIComponent(document.getElementById('fBrand').value);
  var p=encodeURIComponent(document.getElementById('fPriority').value);
  window.location='/sku?q='+q+'&brand='+b+'&priority='+p;
}
</script>
{% endblock %}"""

# ── DEADSTOCK ──────────────────────────────────────────────────────────
@app.route('/deadstock')
@login_required
def deadstock():
    df = get_main_df()
    if df.empty: return redirect('/import')
    dead = df[(df['avg_daily']==0)&(df['stock_eff']>0)].copy()
    dead['nilai_modal'] = dead['stock_eff'] * dead['buy_price']
    dead = dead.sort_values('nilai_modal', ascending=False)
    total_val = int(dead['nilai_modal'].sum())
    rows = [{'brand':r['brand'],'product':str(r['product_full'])[:55],'sku':r['sku'],
             'stock':int(r['stock_eff']),'harga':f"Rp {int(r['buy_price']):,}",
             'nilai':f"Rp {int(r['nilai_modal']):,}",'nilai_raw':int(r['nilai_modal'])}
            for _,r in dead.head(500).iterrows()]
    return render(DEAD_HTML, title='Deadstock', active='deadstock',
                  rows=rows, total_val=f'{total_val:,}', total_sku=len(dead))

DEAD_HTML = """{% block content %}
<div class="row g-3 mb-3">
  <div class="col-md-3"><div class="card kpi-card" style="border-color:#8B5CF6">
    <div class="kpi-val" style="color:#8B5CF6">{{total_sku}}</div><div class="kpi-lbl">SKU Deadstock</div></div></div>
  <div class="col-md-4"><div class="card kpi-card" style="border-color:#8B5CF6">
    <div class="kpi-val" style="color:#8B5CF6;font-size:20px">Rp {{total_val}}</div><div class="kpi-lbl">Modal Terikat</div></div></div>
</div>
<div class="alert alert-warning small">💡 Modal terikat di barang tidak laku. Aksi: promo besar, bundle produk, atau retur ke supplier.</div>
<div class="card">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0" id="tblDead">
        <thead class="table-light"><tr><th>Brand</th><th>Produk</th><th>SKU</th><th>Stok</th><th>Harga Beli</th><th>Nilai Modal</th></tr></thead>
        <tbody>
        {% for r in rows %}
        <tr>
          <td class="fw-500">{{r.brand}}</td>
          <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{r.product}}</td>
          <td class="text-muted small">{{r.sku}}</td>
          <td class="text-center">{{r.stock}}</td>
          <td class="text-end">{{r.harga}}</td>
          <td class="text-end fw-600" style="color:#8B5CF6">{{r.nilai}}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
{% block scripts %}
<script>$('#tblDead').DataTable({pageLength:50,language:{url:'//cdn.datatables.net/plug-ins/1.13.6/i18n/id.json'}});</script>
{% endblock %}"""

# ── WA BLAST ──────────────────────────────────────────────────────────
@app.route('/blast')
@login_required
def blast():
    customers = db_load('customers', [])
    templates = db_load('blast_templates', DEFAULT_TEMPLATES)
    segs_count = {}
    for c in customers:
        s = c.get('segment','')
        segs_count[s] = segs_count.get(s, 0) + 1
    S = get_settings()
    db = get_db()
    history = [dict(r) for r in db.execute(
        "SELECT * FROM blast_history ORDER BY id DESC LIMIT 10").fetchall()]
    db.close()
    return render(BLAST_HTML, title='WA Blast CRM', active='blast',
                  customers_count=len(customers), segs=segs_count,
                  templates=templates, history=history,
                  has_token=bool(S.get('fonnte_token')),
                  delay=S.get('blast_delay',4))

BLAST_HTML = """{% block content %}
<div class="row g-3">
  <div class="col-md-4">
    <div class="card p-3 mb-3">
      <h6 class="fw-600 mb-3">👥 Data Customer</h6>
      <div class="text-center py-2">
        <div style="font-size:32px;font-weight:700;color:#E91E63">{{customers_count}}</div>
        <div class="text-muted small">Total customer</div>
      </div>
      <hr>
      <div class="small">
        {% for seg, n in segs.items() %}
        <div class="d-flex justify-content-between py-1 border-bottom">
          <span>{{seg}}</span><span class="fw-600">{{n}}</span>
        </div>
        {% endfor %}
      </div>
      <button class="btn btn-sm btn-outline-secondary mt-2 w-100" onclick="$('#modalImport').modal('show')">📥 Import Customer</button>
    </div>
    <div class="card p-3">
      <h6 class="fw-600 mb-3">📜 Riwayat Blast</h6>
      {% if history %}
      {% for h in history %}
      <div class="small py-2 border-bottom">
        <div class="fw-500">{{h.created_at}}</div>
        <div class="text-muted">{{h.total}} customer · <span class="text-success">{{h.sent}} terkirim</span> · <span class="text-danger">{{h.failed}} gagal</span></div>
      </div>
      {% endfor %}
      {% else %}<div class="text-muted small">Belum ada riwayat</div>{% endif %}
    </div>
  </div>
  <div class="col-md-8">
    <div class="card p-3 mb-3">
      <h6 class="fw-600 mb-3">✏️ Template Pesan per Segmen</h6>
      <div class="row g-2 mb-2">
        <div class="col-md-4">
          <select class="form-select form-select-sm" id="segSelect" onchange="loadTemplate()">
            {% for seg in templates %}<option value="{{seg}}">{{seg}}</option>{% endfor %}
          </select>
        </div>
        <div class="col-md-8">
          <div class="text-muted small mt-1">Variabel: <code>{nama}</code> <code>{segment}</code></div>
        </div>
      </div>
      <textarea class="form-control" id="tplEditor" rows="6" style="font-size:12px;font-family:monospace"></textarea>
      <div class="d-flex gap-2 mt-2">
        <button class="btn btn-sm btn-pink" onclick="saveTemplate()">💾 Simpan Template</button>
        <div id="tplMsg" class="small text-success mt-1"></div>
      </div>
    </div>
    <div class="card p-3">
      <h6 class="fw-600 mb-3">🚀 Kirim Blast</h6>
      {% if not has_token %}
      <div class="alert alert-warning small">⚠️ Token Fonnte belum diisi. <a href="/settings">Setup di Settings →</a></div>
      {% endif %}
      <div class="mb-3">
        <label class="form-label small">Pilih Segmen</label>
        <div id="segChecks">
          {% for seg, n in segs.items() %}
          <div class="form-check form-check-inline">
            <input class="form-check-input" type="checkbox" id="seg_{{loop.index}}" value="{{seg}}" checked>
            <label class="form-check-label small" for="seg_{{loop.index}}">{{seg}} ({{n}})</label>
          </div>
          {% endfor %}
        </div>
      </div>
      <div class="row g-2 mb-3">
        <div class="col-md-4">
          <label class="form-label small">Delay antar pesan (detik)</label>
          <input type="number" class="form-control form-control-sm" id="blastDelay" value="{{delay}}" min="2" max="30">
        </div>
        <div class="col-md-4">
          <label class="form-label small">Mode</label>
          <select class="form-select form-select-sm" id="blastMode">
            <option value="live">Live (kirim WA nyata)</option>
            <option value="simulate">Simulasi (test)</option>
          </select>
        </div>
      </div>
      <div class="d-flex gap-2 mb-3">
        <button class="btn btn-pink" id="btnBlast" onclick="startBlast()" {% if not has_token %}disabled{% endif %}>🚀 Mulai Blast</button>
        <button class="btn btn-outline-danger d-none" id="btnStop" onclick="stopBlast()">⏹ Stop</button>
      </div>
      <div id="progWrap" class="d-none">
        <div class="d-flex gap-3 mb-2 small">
          <span id="statSent" class="text-success fw-600">0 terkirim</span>
          <span id="statFail" class="text-danger">0 gagal</span>
          <span id="statSkip" class="text-muted">0 skip</span>
          <span id="statTotal" class="text-muted"></span>
        </div>
        <div class="prog-bar-custom"><div class="prog-fill" id="progFill" style="width:0"></div></div>
      </div>
      <div class="log-box mt-2" id="logBox"><div style="color:#555">Log akan muncul di sini...</div></div>
    </div>
  </div>
</div>
<!-- Import Modal -->
<div class="modal fade" id="modalImport" tabindex="-1">
  <div class="modal-dialog"><div class="modal-content">
    <div class="modal-header"><h6 class="modal-title">Import Data Customer</h6>
      <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
    <div class="modal-body">
      <p class="small text-muted">Upload file CSV/Excel dengan kolom: <strong>nama, nomor_hp, segment, poin</strong></p>
      <input type="file" class="form-control" id="custFile" accept=".csv,.xlsx,.xls">
      <div id="importMsg" class="mt-2 small"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-sm btn-pink" onclick="importCustomers()">Import</button>
    </div>
  </div></div>
</div>
{% endblock %}
{% block scripts %}
<script>
const templates = {{templates|tojson}};
function loadTemplate(){
  var seg = document.getElementById('segSelect').value;
  document.getElementById('tplEditor').value = templates[seg] || '';
}
loadTemplate();
function saveTemplate(){
  var seg = document.getElementById('segSelect').value;
  var tpl = document.getElementById('tplEditor').value;
  templates[seg] = tpl;
  fetch('/api/blast/template', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({segment:seg,template:tpl})})
  .then(r=>r.json()).then(d=>{document.getElementById('tplMsg').textContent='Tersimpan!';setTimeout(()=>document.getElementById('tplMsg').textContent='',2000);});
}
function getActiveSegs(){return [...document.querySelectorAll('#segChecks input:checked')].map(i=>i.value);}
function startBlast(){
  var segs=getActiveSegs();
  if(!segs.length){alert('Pilih minimal 1 segmen!');return;}
  var mode=document.getElementById('blastMode').value;
  var delay=parseInt(document.getElementById('blastDelay').value)||4;
  document.getElementById('btnBlast').disabled=true;
  document.getElementById('btnStop').classList.remove('d-none');
  document.getElementById('progWrap').classList.remove('d-none');
  fetch('/api/blast/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({segments:segs,mode:mode,delay:delay})})
  .then(r=>r.json()).then(d=>{if(d.ok)pollBlast();else alert(d.error);});
}
function stopBlast(){fetch('/api/blast/stop',{method:'POST'});}
var pollInterval;
function pollBlast(){
  pollInterval=setInterval(()=>{
    fetch('/api/blast/status').then(r=>r.json()).then(s=>{
      var pct=s.total?Math.round(s.idx/s.total*100):0;
      document.getElementById('progFill').style.width=pct+'%';
      document.getElementById('statSent').textContent=s.sent+' terkirim';
      document.getElementById('statFail').textContent=s.failed+' gagal';
      document.getElementById('statSkip').textContent=s.skipped+' skip';
      document.getElementById('statTotal').textContent=s.idx+'/'+s.total;
      var lb=document.getElementById('logBox');
      lb.innerHTML=s.log.slice(-50).map(l=>'<div class="log-'+l.type+'">['+l.t+'] '+l.msg+'</div>').join('');
      lb.scrollTop=lb.scrollHeight;
      if(!s.running){clearInterval(pollInterval);document.getElementById('btnBlast').disabled=false;document.getElementById('btnStop').classList.add('d-none');}
    });
  },700);
}
function importCustomers(){
  var file=document.getElementById('custFile').files[0];
  if(!file)return;
  var fd=new FormData();fd.append('file',file);
  fetch('/api/customers/import',{method:'POST',body:fd})
  .then(r=>r.json()).then(d=>{
    document.getElementById('importMsg').className='small '+(d.ok?'text-success':'text-danger');
    document.getElementById('importMsg').textContent=d.ok?'Import '+d.count+' customer berhasil!':d.error;
    if(d.ok)setTimeout(()=>location.reload(),1500);
  });
}
</script>
{% endblock %}"""

# ── CUSTOMERS ──────────────────────────────────────────────────────────
@app.route('/customers')
@login_required
def customers():
    custs = db_load('customers', [])
    return render(CUST_HTML, title='Customers', active='customers',
                  customers=custs[:500], total=len(custs))

CUST_HTML = """{% block content %}
<div class="d-flex justify-content-between mb-3">
  <div class="text-muted small">Total: <strong>{{total}}</strong> customer</div>
  <button class="btn btn-sm btn-pink" onclick="$('#modalImport').modal('show')">📥 Import</button>
</div>
<div class="card">
  <div class="card-body p-0">
    <table class="table table-sm table-hover mb-0" id="tblCust">
      <thead class="table-light"><tr><th>#</th><th>Nama</th><th>No HP</th><th>Segmen</th><th>Poin</th></tr></thead>
      <tbody>
      {% for c in customers %}
      <tr><td>{{loop.index}}</td><td>{{c.nama}}</td><td class="text-muted">{{c.nomor_hp}}</td>
        <td><small class="badge bg-secondary">{{c.segment}}</small></td><td>{{c.poin}}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
<div class="modal fade" id="modalImport" tabindex="-1">
  <div class="modal-dialog"><div class="modal-content">
    <div class="modal-header"><h6 class="modal-title">Import Customer</h6>
      <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
    <div class="modal-body">
      <p class="small text-muted">Kolom: nama, nomor_hp, segment, poin</p>
      <input type="file" class="form-control" id="custFile2" accept=".csv,.xlsx,.xls">
      <div id="importMsg2" class="mt-2 small"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-sm btn-pink" onclick="importCust()">Import</button>
    </div>
  </div></div>
</div>
{% endblock %}
{% block scripts %}
<script>
$('#tblCust').DataTable({pageLength:50,language:{url:'//cdn.datatables.net/plug-ins/1.13.6/i18n/id.json'}});
function importCust(){
  var file=document.getElementById('custFile2').files[0];
  if(!file)return;
  var fd=new FormData();fd.append('file',file);
  fetch('/api/customers/import',{method:'POST',body:fd})
  .then(r=>r.json()).then(d=>{
    document.getElementById('importMsg2').className='small '+(d.ok?'text-success':'text-danger');
    document.getElementById('importMsg2').textContent=d.ok?d.count+' customer diimport!':d.error;
    if(d.ok)setTimeout(()=>location.reload(),1500);
  });
}
</script>
{% endblock %}"""

# ── IMPORT DATA ────────────────────────────────────────────────────────
@app.route('/import')
@login_required
def import_page():
    sales_ts = db_ts('sales_data')
    stock_ts = db_ts('stock_data')
    sales_count = len(db_load('sales_data') or [])
    stock_count = len(db_load('stock_data') or [])
    return render(IMPORT_HTML, title='Import Data', active='import',
                  sales_ts=sales_ts, stock_ts=stock_ts,
                  sales_count=sales_count, stock_count=stock_count)

IMPORT_HTML = """{% block content %}
<div class="row g-3">
  <div class="col-md-6">
    <div class="card p-4">
      <h6 class="fw-600 mb-1">📊 Upload Data Penjualan</h6>
      <p class="small text-muted mb-3">Export dari POS: Laporan → Item Penjualan Berdasarkan Brand → Export Excel. Boleh upload dari 3 toko sekaligus.</p>
      {% if sales_ts %}<div class="alert alert-success small py-2">✅ Terakhir diupdate: {{sales_ts}} · {{sales_count}} SKU</div>{% endif %}
      <input type="file" class="form-control mb-2" id="salesFile" accept=".csv,.xlsx,.xls" multiple>
      <div class="mb-2">
        <label class="form-label small">Periode data (hari)</label>
        <input type="number" class="form-control form-control-sm" id="periodDays" value="21" min="1" max="90">
      </div>
      <button class="btn btn-pink w-100" onclick="uploadSales()">Upload & Simpan Penjualan</button>
      <div id="salesMsg" class="mt-2 small"></div>
    </div>
  </div>
  <div class="col-md-6">
    <div class="card p-4">
      <h6 class="fw-600 mb-1">📦 Upload Data Stok</h6>
      <p class="small text-muted mb-3">Export dari POS: Laporan → Sisa Stok Produk → Export Excel. Boleh dari 3 toko sekaligus.</p>
      {% if stock_ts %}<div class="alert alert-success small py-2">✅ Terakhir diupdate: {{stock_ts}} · {{stock_count}} SKU</div>{% endif %}
      <input type="file" class="form-control mb-2" id="stockFile" accept=".csv,.xlsx,.xls" multiple>
      <button class="btn btn-pink w-100" onclick="uploadStock()">Upload & Simpan Stok</button>
      <div id="stockMsg" class="mt-2 small"></div>
    </div>
  </div>
</div>
<div class="card mt-3 p-3">
  <h6 class="fw-600 mb-2">ℹ️ Cara Kerja Import</h6>
  <div class="row g-3 small text-muted">
    <div class="col-md-4"><strong class="text-dark">Auto-detect kolom</strong><br>Sistem otomatis cari kolom SKU & Qty dari file apapun — tidak perlu format khusus.</div>
    <div class="col-md-4"><strong class="text-dark">Multi-toko sekaligus</strong><br>Upload 3 file dari CG, PL, UMY sekaligus — sistem SUMIF otomatis per SKU.</div>
    <div class="col-md-4"><strong class="text-dark">Persistent</strong><br>Data tersimpan di database lokal — tidak hilang saat browser ditutup.</div>
  </div>
</div>
{% endblock %}
{% block scripts %}
<script>
function showMsg(id, msg, ok){
  var el=document.getElementById(id);
  el.className='mt-2 small '+(ok?'text-success':'text-danger');
  el.textContent=msg;
}
async function uploadSales(){
  var files=document.getElementById('salesFile').files;
  if(!files.length){showMsg('salesMsg','Pilih file dulu!',false);return;}
  var period=document.getElementById('periodDays').value;
  var allData=[];
  showMsg('salesMsg','Memproses...', true);
  for(var i=0;i<files.length;i++){
    var fd=new FormData();fd.append('file',files[i]);fd.append('type','sales');
    var r=await fetch('/api/import',{method:'POST',body:fd});
    var d=await r.json();
    if(!d.ok){showMsg('salesMsg','Error '+files[i].name+': '+d.error,false);return;}
    allData=allData.concat(d.data);
  }
  var agg={};
  allData.forEach(r=>{agg[r.sku]=(agg[r.sku]||0)+r.qty;});
  var final=Object.entries(agg).map(([sku,qty])=>({sku,qty}));
  var r2=await fetch('/api/import/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'sales',data:final,period_days:parseInt(period)})});
  var d2=await r2.json();
  showMsg('salesMsg', d2.ok?'✅ '+final.length+' SKU tersimpan!':d2.error, d2.ok);
}
async function uploadStock(){
  var files=document.getElementById('stockFile').files;
  if(!files.length){showMsg('stockMsg','Pilih file dulu!',false);return;}
  var allData=[];
  showMsg('stockMsg','Memproses...', true);
  for(var i=0;i<files.length;i++){
    var fd=new FormData();fd.append('file',files[i]);fd.append('type','stock');
    var r=await fetch('/api/import',{method:'POST',body:fd});
    var d=await r.json();
    if(!d.ok){showMsg('stockMsg','Error '+files[i].name+': '+d.error,false);return;}
    allData=allData.concat(d.data);
  }
  var agg={};
  allData.forEach(r=>{agg[r.sku]=(agg[r.sku]||0)+r.stock;});
  var final=Object.entries(agg).map(([sku,stock])=>({sku,stock}));
  var r2=await fetch('/api/import/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'stock',data:final})});
  var d2=await r2.json();
  showMsg('stockMsg', d2.ok?'✅ '+final.length+' SKU tersimpan!':d2.error, d2.ok);
}
</script>
{% endblock %}"""

# ── SETTINGS ──────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    S = get_settings()
    msg = None
    if request.method == 'POST':
        new_s = {
            'period_days': int(request.form.get('period_days',21)),
            'lead_time':   int(request.form.get('lead_time',3)),
            'buffer_days': int(request.form.get('buffer_days',7)),
            'horizon':     int(request.form.get('horizon',7)),
            'fast_thr':    int(request.form.get('fast_thr',20)),
            'med_thr':     int(request.form.get('med_thr',5)),
            'margin_thr':  int(request.form.get('margin_thr',25)),
            'fonnte_token': request.form.get('fonnte_token','').strip(),
            'blast_delay': int(request.form.get('blast_delay',4)),
        }
        db_save('settings', new_s)
        S = new_s
        msg = 'Settings tersimpan!'
    return render(SETTINGS_HTML, title='Settings', active='settings', S=S, msg=msg)

SETTINGS_HTML = """{% block content %}
{% if msg %}<div class="alert alert-success">{{msg}}</div>{% endif %}
<form method="post">
<div class="row g-3">
  <div class="col-md-6">
    <div class="card p-4">
      <h6 class="fw-600 mb-3">📱 Fonnte WA API</h6>
      <div class="mb-3">
        <label class="form-label small">Device Token Fonnte</label>
        <input type="password" name="fonnte_token" class="form-control" value="{{S.fonnte_token}}" placeholder="Token dari fonnte.com → Device">
        <div class="form-text">Dapatkan di fonnte.com → Profile → Device → salin token</div>
      </div>
      <div class="mb-3">
        <label class="form-label small">Delay Antar Pesan (detik) — min 3</label>
        <input type="number" name="blast_delay" class="form-control" value="{{S.blast_delay}}" min="2" max="30">
      </div>
    </div>
  </div>
  <div class="col-md-6">
    <div class="card p-4">
      <h6 class="fw-600 mb-3">⚙️ Parameter SKU & Purchase</h6>
      <div class="row g-2">
        <div class="col-6"><label class="form-label small">Periode Data (hari)</label>
          <input type="number" name="period_days" class="form-control form-control-sm" value="{{S.period_days}}"></div>
        <div class="col-6"><label class="form-label small">Lead Time Supplier (hari)</label>
          <input type="number" name="lead_time" class="form-control form-control-sm" value="{{S.lead_time}}"></div>
        <div class="col-6"><label class="form-label small">Buffer Stok (hari)</label>
          <input type="number" name="buffer_days" class="form-control form-control-sm" value="{{S.buffer_days}}"></div>
        <div class="col-6"><label class="form-label small">Horizon Planning (hari)</label>
          <input type="number" name="horizon" class="form-control form-control-sm" value="{{S.horizon}}"></div>
        <div class="col-6"><label class="form-label small">Fast Moving (unit/bln)</label>
          <input type="number" name="fast_thr" class="form-control form-control-sm" value="{{S.fast_thr}}"></div>
        <div class="col-6"><label class="form-label small">Medium Moving (unit/bln)</label>
          <input type="number" name="med_thr" class="form-control form-control-sm" value="{{S.med_thr}}"></div>
        <div class="col-12"><label class="form-label small">Threshold Margin Tebal (%)</label>
          <input type="number" name="margin_thr" class="form-control form-control-sm" value="{{S.margin_thr}}"></div>
      </div>
    </div>
  </div>
</div>
<div class="mt-3"><button type="submit" class="btn btn-pink">💾 Simpan Settings</button></div>
</form>
{% endblock %}"""

# ── API ROUTES ────────────────────────────────────────────────────────
@app.route('/api/import', methods=['POST'])
@login_required
def api_import():
    file = request.files.get('file')
    type_ = request.form.get('type','sales')
    if not file: return jsonify({'ok':False,'error':'No file'})
    content = file.read()
    if type_ == 'sales':
        data, err = parse_sales(content, file.filename)
    else:
        data, err = parse_stock(content, file.filename)
    if err: return jsonify({'ok':False,'error':err})
    return jsonify({'ok':True,'data':data,'count':len(data)})

@app.route('/api/import/save', methods=['POST'])
@login_required
def api_import_save():
    body = request.json or {}
    type_ = body.get('type','sales')
    data  = body.get('data',[])
    if type_ == 'sales':
        db_save('sales_data', data)
        S = get_settings(); S['period_days'] = body.get('period_days', S['period_days'])
        db_save('settings', S)
    else:
        db_save('stock_data', data)
    return jsonify({'ok':True,'count':len(data)})

@app.route('/api/customers/import', methods=['POST'])
@login_required
def api_import_customers():
    file = request.files.get('file')
    if not file: return jsonify({'ok':False,'error':'No file'})
    try:
        content = file.read()
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content), encoding='utf-8-sig')
        else:
            df = pd.read_excel(io.BytesIO(content))
        df.columns = [str(c).strip().lower().replace(' ','_') for c in df.columns]
        nama_c  = find_col(df, ['nama','name','customer_name','pelanggan'])
        phone_c = find_col(df, ['nomor_hp','phone','no_hp','hp','wa','whatsapp'])
        seg_c   = find_col(df, ['segment','segmen','segmentation','kategori'])
        poin_c  = find_col(df, ['poin','point','points'])
        if not phone_c: return jsonify({'ok':False,'error':f'Kolom HP tidak ditemukan. Kolom: {list(df.columns)}'})
        custs = []
        for _, r in df.iterrows():
            ph = str(r.get(phone_c,'')).strip()
            nm = str(r.get(nama_c,'')).strip() if nama_c else ''
            if not ph and not nm: continue
            custs.append({
                'nama':    nm,
                'nomor_hp': ph,
                'segment': str(r.get(seg_c,'')).strip() if seg_c else 'Umum',
                'poin':    str(r.get(poin_c,'0')).strip() if poin_c else '0',
            })
        db_save('customers', custs)
        return jsonify({'ok':True,'count':len(custs)})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)})

@app.route('/api/blast/template', methods=['POST'])
@login_required
def api_blast_template():
    body = request.json or {}
    templates = db_load('blast_templates', dict(DEFAULT_TEMPLATES))
    templates[body.get('segment','')] = body.get('template','')
    db_save('blast_templates', templates)
    return jsonify({'ok':True})

@app.route('/api/blast/start', methods=['POST'])
@login_required
def api_blast_start():
    global blast_state
    if blast_state['running']:
        return jsonify({'ok':False,'error':'Blast sudah berjalan'})
    body      = request.json or {}
    S         = get_settings()
    token     = S.get('fonnte_token','')
    mode      = body.get('mode','simulate')
    delay     = int(body.get('delay', S.get('blast_delay',4)))
    segs      = set(body.get('segments',[]))
    customers = db_load('customers',[])
    templates = db_load('blast_templates', dict(DEFAULT_TEMPLATES))
    if mode == 'live' and not token:
        return jsonify({'ok':False,'error':'Token Fonnte belum diisi di Settings'})
    if not customers:
        return jsonify({'ok':False,'error':'Belum ada data customer'})
    if mode == 'simulate':
        # simulate mode
        def sim_blast(customers, templates, token, delay, segs):
            global blast_state
            blast_state.update({'running':True,'log':[],'sent':0,'failed':0,'skipped':0,'idx':0,'total':len(customers)})
            def log(m,t='info'): blast_state['log'].append({'t':datetime.now().strftime('%H:%M:%S'),'msg':m,'type':t})
            log('Mode SIMULASI — tidak ada WA nyata yang terkirim')
            for i,c in enumerate(customers):
                blast_state['idx']=i+1
                seg=c.get('segment','')
                nama=c.get('nama','Customer')
                if seg not in segs: blast_state['skipped']+=1; log(f'[{i+1}] SKIP: {nama}','skip'); continue
                phone=fmt_phone(c.get('nomor_hp',''))
                if not phone or len(phone)<8: blast_state['skipped']+=1; log(f'[{i+1}] SKIP nomor: {nama}','skip'); continue
                time.sleep(0.05)
                blast_state['sent']+=1; log(f'[{i+1}] SIMULASI OK — {nama} ({phone})','ok')
            blast_state['running']=False
            log(f'SELESAI SIMULASI — OK:{blast_state["sent"]} Skip:{blast_state["skipped"]}')
        t = threading.Thread(target=sim_blast, args=(customers,templates,token,delay,segs), daemon=True)
    else:
        t = threading.Thread(target=run_blast, args=(customers,templates,token,delay,segs), daemon=True)
    t.start()
    return jsonify({'ok':True})

@app.route('/api/blast/stop', methods=['POST'])
@login_required
def api_blast_stop():
    blast_state['running'] = False
    return jsonify({'ok':True})

@app.route('/api/blast/status')
@login_required
def api_blast_status():
    return jsonify(blast_state)

@app.route('/api/purchase/download')
@login_required
def api_purchase_download():
    df = get_main_df()
    if df.empty: return 'No data', 404
    filtered = df[df['perlu_beli']>0].sort_values('sort_key')
    out = filtered[['priority_class','brand','product_full','sku',
                    'stock_eff','avg_daily','days_to_out','perlu_beli',
                    'buy_price','po_value','status']].copy()
    out.columns = ['Priority','Brand','Produk','SKU','Stok','Jual/Hari',
                   'Hari s/d Kosong','PERLU BELI','Harga Beli','Nilai PO','Status']
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        out.to_excel(w, index=False, sheet_name='Purchase Order')
    buf.seek(0)
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=f'PO_Merona_{date.today().strftime("%Y%m%d")}.xlsx')

@app.route('/api/purchase/csv')
@login_required
def api_purchase_csv():
    df = get_main_df()
    if df.empty: return 'No data', 404
    filtered = df[df['perlu_beli']>0].sort_values('sort_key')
    buf = io.StringIO()
    filtered[['priority_class','brand','product_full','sku','stock_eff',
              'avg_daily','perlu_beli','buy_price','po_value','status']].to_csv(buf, index=False)
    return send_file(io.BytesIO(buf.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'purchase_{date.today().strftime("%Y%m%d")}.csv')

@app.route('/api/sku/download')
@login_required
def api_sku_download():
    df = get_main_df()
    if df.empty: return 'No data', 404
    out = df[['priority_class','brand','product_full','sku','stock_eff',
              'avg_daily','monthly_est','margin_pct','buy_price','sell_price',
              'perlu_beli','status']].copy()
    out.columns = ['Priority','Brand','Produk','SKU','Stok','Jual/Hari',
                   'Est/Bln','Margin%','Harga Beli','Harga Jual','Perlu Beli','Status']
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        out.to_excel(w, index=False, sheet_name='SKU Matrix')
    buf.seek(0)
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=f'SKU_{date.today().strftime("%Y%m%d")}.xlsx')

# ── MAIN ──────────────────────────────────────────────────────────────
# Init on startup (works with gunicorn too)
init_db()
load_baseline()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
