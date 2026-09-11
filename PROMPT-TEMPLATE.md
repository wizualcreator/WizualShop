# Template Prompt Awal — Bot Telegram Toko Produk Digital

> Salin teks di bawah, ganti semua `[PLACEHOLDER]` dengan milikmu, lalu paste ke AI.

---

Buatkan bot Telegram toko produk digital lengkap bernama **[NAMA_TOKO]**
dengan Python. Produk & stok dikelola di Google Sheets; bot membaca sheet
via export CSV (gviz) dan menulis-balik hasil penjualan lewat Apps Script
web app. Hosting di Render (polling, disk ephemeral).

## Arsitektur
- Python + python-telegram-bot v20 (async, polling, JobQueue) + requests
  + Pillow + sqlite3 + python-dotenv.
- 4 sheet: PRODUCTS (ID, NAME, EMOJI, PRICE, STATUS, DESCRIPTION),
  STOCK (STOCK_ID, PRODUCT_ID, CONTENT, STATUS, SOLD_TO),
  SETTINGS (KEY, VALUE), ORDERS (ORDER_ID, TELEGRAM_ID, USERNAME,
  PRODUCT_ID, QTY, TOTAL, STATUS, PAYMENT_ID, CREATED_AT, PAID_AT).
- Apps Script "sheet-write-back.gs": endpoint POST + secret, menerima
  mark_sold (set STATUS=SOLD + SOLD_TO) dan orders (upsert), rate-limit.

## Fitur user
- /start (banner + force-join channel), /menu, /products, /promo, /stock,
  /orders, /help, /join, /support, /affiliate.
- Home: greeting + daftar produk, tombol inline (Gas Belanja, Promo, Cek
  Stok, Referral, Bantuan, Refresh). Semua halaman edit-inline, HTML,
  safe_edit dengan fallback kirim pesan baru.
- Katalog → halaman produk (qty −/+, batas stok) → checkout.

## Alur pembayaran (PAYMENT_METHOD)
- static_qris: tampil QRIS + nominal, tombol "Saya Sudah Bayar" →
  AWAITING_ADMIN → admin approve/reject (notif inline).
- nevapedia: create invoice (QRIS di-frame Pillow border **[WARNA_BORDER, contoh #144cf9]**),
  tombol "Cek Status" + polling 60s → auto-complete saat paid.
- TEST_MODE: pembayaran disimulasikan.

## Anti over-sell (PENTING)
- Stok di-RESERVED saat payment intent; claim stok atomik (BEGIN IMMEDIATE,
  compare-and-set WHERE status='AVAILABLE'/'RESERVED'), first-payment-wins;
  cleanup reservasi >24 jam; PAID_BUT_OUT_OF_STOCK + notif admin.

## Delivery
- Saat order COMPLETED: kirim produk sebagai file .txt via sendDocument
  (+ terms umum & khusus produk tertentu), flag delivered idempotent,
  write-back stok SOLD + order ke sheet, notif admin & channel.

## Afiliasi
- Link https://t.me/<bot>?start=ref_UID, komisi **[PERSEN_KOMISI, contoh 5]**% per
  order sukses; tabel referrals, commissions, wallets; menu /affiliate,
  admin /affiliates & /payout.

## Ketahanan ephemeral disk
- restore_inflight_orders dari sheet ORDERS; write-back retry 3x + notif
  admin jika gagal; to_thread semua I/O blocking; lock sync; health server
  di PORT + keep-alive RENDER_EXTERNAL_URL.

## Konfigurasi (.env)
TOKEN, ADMIN_CHAT_ID, CHANNEL_USERNAME, SPREADSHEET_ID, PAYMENT_METHOD,
AFFILIATE_PERCENT, NEVAPEDIA_API_KEY, QRIS_IMAGE_URL, TEST_MODE,
SHEET_WRITE_SECRET, SHEET_WRITE_URL, BANNER_URL, PORT, RENDER_EXTERNAL_URL.

## Keamanan
Tanpa secret hardcoded; escape semua input di HTML; validasi URL sheet &
QRIS; rate-limit Apps Script; test pytest (race condition stok).

## Pesan UI yang WAJIB dipertahankan
- Header: "✦ **[NAMA_TOKO]** ✦" + tagline **[TAGLINE_TOKO]**.
- Home: "**[KALIMAT_PROMO, contoh: 🔥 Kalau yang lagi hot ada:]**" → daftar produk
  "{emoji} <b>{nama}</b> / Rp{harga} · 🟢 N ready|⏳ sold out", dipisah
  spasi lega.
- Branding konsisten di semua halaman.

---

## Catatan pengisian placeholder

| Placeholder | Contoh |
|---|---|
| [NAMA_TOKO] | DIGITALIN STORE |
| [TAGLINE_TOKO] | Your Digital Playground |
| [KALIMAT_PROMO] | 🔥 Kalau yang lagi hot ada: |
| [CHANNEL] | @NamaChannelUpdate |
| [WARNA_BORDER] | #144cf9 |
| [PERSEN_KOMISI] | 5 |