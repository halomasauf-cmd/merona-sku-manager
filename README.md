# Merona SKU Manager

Web app untuk manajemen SKU, purchase planning, dan deadstock analysis.

## Cara Jalankan (Lokal)

```bash
# 1. Install dependencies
pip3 install streamlit pandas openpyxl plotly

# 2. Jalankan
streamlit run app.py

# 3. Buka browser → http://localhost:8501
```

## Cara Deploy ke Cloud (GRATIS)

1. Buat akun di https://github.com (gratis)
2. Upload folder ini ke GitHub repository baru
3. Buka https://share.streamlit.io
4. Login dengan GitHub → klik "New App" → pilih repository
5. Main file: `app.py` → klik Deploy
6. ✅ App online dalam 2-3 menit — bagikan link ke tim!

## Struktur File

```
merona_sku_app/
├── app.py                 ← Main app
├── baseline_data.gz       ← Data baseline (Mei 2026)
├── requirements.txt       ← Dependencies
└── README.md              ← Panduan ini
```

## Cara Update Data (Setiap Minggu)

1. Buka app → menu **📥 Import Data**
2. Upload file penjualan dari POS (Excel/CSV)
3. Upload file stok terkini dari POS
4. Klik **✅ Simpan**
5. Buka **🚨 Purchase Planner** → lihat daftar belanja!

## Fitur

- 🏠 **Dashboard** — overview KPI, charts distribusi, top brand
- 🚨 **Purchase Planner** — warning 🔴🟠🟡✅, download PO
- 🏆 **SKU Classification** — Fast/Medium/Slow, filter & search
- 📥 **Import Data** — upload Excel/CSV dari POS langsung
- 💤 **Deadstock** — analisis modal terikat
- ⚙️ **Settings** — konfigurasi threshold & parameter
