# PRD — Bot Telegram Toko Produk Digital "DIGITALIN STORE"

> Rekonstruksi dari kode produksi + git history. Dipakai sebagai prompt untuk membuat bot dari awal.

## 1. Ringkasan

Bot Telegram untuk menjual produk digital (akun/kode/akses premium) secara otomatis.
Produk & stok dikelola di **Google Sheets**, bot membaca via export CSV dan menulis-balik
hasil penjualan (stok SOLD + order) lewat **Apps Script web app**. Hosting di **Render**
(disk ephemeral, polling, health check, keep-alive).

## 2. Stack

- Python 3, `python-telegram-bot` v20 (async, polling, `JobQueue`), `requests`, `Pillow`, `sqlite3`, `python-dotenv`.
- Google Sheets sebagai sumber kebenaran produk/stok/pengaturan/order.
- Apps Script `sheet-write-back.gs` untuk write-back (POST JSON + secret).
- Payment: `static_qris` (verifikasi admin) ATAU `nevapedia` (gateway QRIS otomatis).
- Deploy Render: `render.yaml`, `Procfile`, health server di port `PORT`, `RENDER_EXTERNAL_URL` untuk keep-alive.

## 3. Google Sheets — struktur

- **PRODUCTS**: `ID, NAME, EMOJI, PRICE, STATUS, DESCRIPTION` (STATUS `ACTIVE`).
- **STOCK**: `STOCK_ID, PRODUCT_ID, CONTENT, STATUS, SOLD_TO` (STATUS: `AVAILABLE`/`SOLD`; CONTENT = kredensial produk).
- **SETTINGS**: `KEY, VALUE` (mis. `ADMIN_USERNAME`, `STORE_NAME`).
- **ORDERS**: `ORDER_ID, TELEGRAM_ID, USERNAME, PRODUCT_ID, QTY, TOTAL, STATUS, PAYMENT_ID, CREATED_AT, PAID_AT`.

Bot membaca semua sheet via `gviz/tq?tqx=out:csv&sheet=...`. Write-back lewat Apps Script
(`mark_sold` → set STATUS SOLD + SOLD_TO; `orders` → upsert baris order).

## 4. Alur inti

1. **Sinkronisasi**: tarik semua sheet ke SQLite lokal (produk upsert, stok upsert atomik
   — baris SOLD/RESERVED tidak boleh ditimpa resync; stok lokal yang tidak ada di sheet
   dihapus kecuali mencurigakan; setting di-cache).
2. **Force join channel** sebelum belanja (`CHANNEL_USERNAME`), tombol Join + "✅ Sudah Join", fail-open.
3. **Home/katalog/promo/stok/pesanan/bantuan/afiliasi** — halaman inline-keyboard HTML.
4. **Checkout**: pilih qty (−/+), total otomatis, buat order `ORD-XXXXXXXXXX` → status `PENDING`.
5. **Pembayaran**:
   - `static_qris`: tampil QRIS + nominal, tombol "✅ Saya Sudah Bayar" → `AWAITING_ADMIN` → admin approve/reject.
   - `nevapedia`: create invoice (QRIS image di-frame Pillow border `#144cf9`), tombol "↻ Cek Status" + polling tiap 60s → auto-complete saat paid.
   - `TEST_MODE=true`: pembayaran disimulasikan.
6. **Complete** (atomik, first-payment-wins): claim stok AVAILABLE/RESERVED → SOLD, kirim produk sebagai **file .txt** via `sendDocument`, tandai `delivered`, write-back stok SOLD + order ke sheet, notif admin + channel, hitung komisi afiliasi.
7. **Anti over-sell**: stok di-`RESERVED` saat payment intent dibuat; cleanup reservasi > 24 jam; status `PAID_BUT_OUT_OF_STOCK` + notif admin jika sudah bayar tapi stok habis.

## 5. Fitur

### User
- `/start` (support referral `ref_UID`), `/menu`, `/products`, `/promo`, `/stock`, `/orders`, `/help`, `/join`, `/support`, `/affiliate`.
- Home: greeting + daftar produk hot (`🔥 Kalau yang lagi hot ada:`), tombol Gas Belanja / Promo / Cek Stok / Referral / Bantuan / Refresh.
- Katalog & halaman produk: qty −/+ (batas stok), Checkout, tombol kembali.
- Pesanan saya: daftar order dengan status icon.

### Admin (ADMIN_CHAT_ID)
- `/admin` panel ringkasan (produk aktif, stok, pesanan menunggu/selesai).
- Approve/Reject pembayaran `AWAITING_ADMIN` lewat tombol inline di notifikasi.
- `/affiliates` daftar referrer+saldo; `/payout <uid>` menandai komisi PAID + reset saldo.
- Manajemen produk/stok = edit Google Sheets langsung.

### Afiliasi
- Link `https://t.me/<bot>?start=ref_UID`, komisi `AFFILIATE_PERCENT`% (default 5) per order sukses.
- Tabel `referrals`, `commissions`, `wallets`; notif komisi; pencairan manual admin.

### Ketahanan (ephemeral disk / redeploy)
- `restore_inflight_orders()`: order PENDING/AWAITING_ADMIN/PAID_BUT_OUT_OF_STOCK di-restore dari sheet ORDERS.
- Write-back retry 3x; notif admin jika write-back stok gagal (cek sheet manual).
- Delivery idempotent (flag `delivered` + tombol resend via "Cek Status").
- `check_payments` polling + order PENDING > 48 jam otomatis FAILED.

## 6. Konfigurasi (.env)

`TOKEN`, `ADMIN_CHAT_ID`, `CHANNEL_USERNAME`, `SPREADSHEET_ID`, `PAYMENT_METHOD`
(`static_qris`|`nevapedia`), `AFFILIATE_PERCENT`, `NEVAPEDIA_API_KEY`, `QRIS_IMAGE_URL`,
`TEST_MODE`, `SHEET_WRITE_SECRET`, `SHEET_WRITE_URL`, `BANNER_URL`, `PORT`, `RENDER_EXTERNAL_URL`.

## 7. Keamanan & stabilisasi

- Tidak ada secret hardcoded (semua dari env/script properties).
- Rate-limit Apps Script (240 req/60s), validasi `stock_id` regex, validasi URL sheet & URL QRIS (anti SSRF).
- `asyncio.to_thread` untuk semua I/O blocking; lock sync global; `BEGIN IMMEDIATE` untuk reservasi/claim.
- `safe_edit` dengan fallback kirim pesan baru saat BadRequest "not modified".
- HTML escape semua nama produk (anti-injection).
- Komisi afiliasi nonaktif saat `TEST_MODE`.
- Konten produk dikirim sebagai file .txt (bukan teks HTML) + terms umum & khusus "Google AI Pro 18 Bulan" (garansi nol karena dikelola Jio).

## 8. Test

`test_inventory.py` (race condition claim stok, reservasi) & `test_audit_fixes.py` (audit keamanan/stabilitas) — pytest.

## 9. Skala prioritas (rekonstruksi bertahap dari git history)

1. Kerangka bot + sync sheet + home/katalog/stok + QRIS statis + admin approve.
2. Program afiliasi (referral, komisi, payout).
3. Payment gateway Nevapedia + polling + auto-complete.
4. Delivery produk .txt + terms khusus.
5. Anti over-sell (reservasi, atomic claim), audit keamanan/stabilitas.
6. Frame QRIS Pillow, restore inflight order, write-back retry, cleanup reservasi, testing.

## 10. Pesan kunci (karena diminta sebelumnya, jangan diubah)

- Brand header: `✦ <b>DIGITALIN STORE</b> ✦` + tagline `Your Digital Playground`.
- Home: `👋 Hai, <b>nama</b>!` / `Mau upgrade digital apa hari ini?` / `🔥 Kalau yang lagi hot ada:` / `Pilih untuk mulai belanja 👇`.
- Produk: `{emoji} <b>{nama}</b>` / `   Rp{harga:,} · 🟢 {n} ready|⏳ sold out`.
- Daftar produk dipisah spasi lega (bukan garis pemisah).