import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import db

BRAND = "WIZUAL SHOP"


def esc(s):
    return html.escape(str(s))


def header(title=None):
    lines = [f"✦ <b>{BRAND}</b> ✦", "Level Up Your Digital Life"]
    if title:
        lines.append("")
        lines.append(title)
    return "\n".join(lines)


def fmt_price(n):
    return f"Rp{n:,}"


def force_join_page():
    channel = config.CHANNEL_USERNAME
    channel_link = f"https://t.me/{channel.lstrip('@')}"
    text = (
        f"{header()}\n\n"
        f"Sebelum mulai belanja, kamu harus <b>join channel</b> kami dulu ya!\n\n"
        f"📢 Channel: {channel}\n\n"
        f"Klik tombol di bawah untuk join, lalu klik <b>✅ Sudah Join</b>."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join Channel", url=channel_link)],
            [InlineKeyboardButton("✅ Sudah Join", callback_data="checkjoin")],
        ]
    )
    return text, keyboard


def product_line(p):
    avail = db.count_available(p["id"])
    ready = f"🟢 {avail} ready" if avail > 0 else "⏳ sold out"
    return (
        f"{p['emoji']} <b>{esc(p['name'])}</b>\n"
        f"   {fmt_price(p['price'])} · {ready}"
    )


def home_text(user_name=None):
    products = db.get_active_products()
    greeting = f"👋 Hai, <b>{esc(user_name)}</b>!" if user_name else "👋 Halo!"

    blocks = []
    for p in products:
        avail = db.count_available(p["id"])
        ready = f"🟢 {avail} ready" if avail > 0 else "⏳ sold out"
        blocks.append(
            f"{p['emoji']} <b>{esc(p['name'])}</b>\n"
            f"   {fmt_price(p['price'])} · {ready}"
        )
    hot_section = "\n\n".join(blocks) if blocks else "Belum ada produk tersedia."

    text = (
        f"{greeting}\n"
        f"Mau upgrade digital apa hari ini?\n"
        f"🔥 Kalau yang lagi hot ada:\n\n"
        f"{hot_section}\n\n"
        f"Pilih untuk mulai belanja 👇"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Gas Belanja", callback_data="catalog")],
            [
                InlineKeyboardButton("🔥 Promo", callback_data="promo"),
                InlineKeyboardButton("📦 Cek Stok", callback_data="stock"),
            ],
            [
                InlineKeyboardButton("🤝 Referral", callback_data="affiliate"),
                InlineKeyboardButton("🎧 Bantuan", callback_data="contact"),
            ],
            [InlineKeyboardButton("↻ Refresh", callback_data="refresh")],
        ]
    )
    return text, keyboard


def promo_page():
    products = sorted(db.get_active_products(), key=lambda p: p["price"])
    if not products:
        text = f"{header('<b>🔥 Promo</b>')}\n\nBelum ada promo aktif."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
        )
        return text, keyboard
    parts = [header("<b>🔥 Promo Spesial</b>")]
    blocks = []
    for p in products:
        blocks.append(product_line(p))
    parts.append("\n\n".join(blocks))
    text = "\n\n".join(parts) + "\n\nStok promo terbatas, gas belanja sekarang!"
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Gas Belanja", callback_data="catalog")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def catalog_text():
    products = db.get_active_products()
    if not products:
        text = f"{header('<b>🛍️ Belanja</b>')}\n\nBelum ada produk tersedia saat ini."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
        )
        return text, keyboard

    parts = [header("<b>🛍️ Pilih Produk</b>")]
    blocks = []
    for p in products:
        blocks.append(product_line(p))
    parts.append("\n\n".join(blocks))
    text = "\n\n".join(parts) + "\n\nPilih produk untuk lihat detailnya 👇"

    rows = [
        [InlineKeyboardButton(f"{p['emoji']} {esc(p['name'])}", callback_data=f"product:{p['id']}")]
        for p in products
    ]
    rows.append(
        [
            InlineKeyboardButton("📦 Cek Stok", callback_data="stock"),
            InlineKeyboardButton("⌂ Home", callback_data="home"),
        ]
    )
    return text, InlineKeyboardMarkup(rows)


def product_page(product, qty):
    avail = db.count_available(product["id"])
    total = product["price"] * qty
    sold_out = avail < 1

    text = (
        f"💎 <b>{esc(product['name'])}</b>\n\n"
        f"{esc(product['description'])}\n\n"
        f"💰 <b>{fmt_price(product['price'])}</b>\n"
        f"{'🟢 Ready' if avail > 0 else '⏳ Sold out'} · {avail} akun\n\n"
        f"✨ Instant Delivery\n"
        f"⚡ Proses Otomatis\n\n"
        f"Mau ambil berapa?\n\n"
        f"<b>Total</b>\n"
        f"<b>{fmt_price(total)}</b>"
    )

    if sold_out:
        rows = [
            [InlineKeyboardButton("⏳ Stok Habis", callback_data="noop")],
            [
                InlineKeyboardButton("‹ Kembali", callback_data="catalog"),
                InlineKeyboardButton("⌂ Home", callback_data="home"),
            ],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton("−", callback_data=f"qtydec:{product['id']}"),
                InlineKeyboardButton(f"{qty}", callback_data="noop"),
                InlineKeyboardButton("+", callback_data=f"qtyinc:{product['id']}"),
            ],
            [InlineKeyboardButton("⚡ Checkout Sekarang", callback_data=f"buy:{product['id']}")],
            [
                InlineKeyboardButton("‹ Kembali", callback_data="catalog"),
                InlineKeyboardButton("⌂ Home", callback_data="home"),
            ],
        ]
    return text, InlineKeyboardMarkup(rows)


def stock_page():
    products = db.get_active_products()
    parts = [header("<b>📦 Cek Stok</b>")]
    if not products:
        parts.append("Belum ada produk tersedia.")
    for p in products:
        parts.append(product_line(p))
    text = "\n\n".join(parts)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Gas Belanja", callback_data="catalog")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def orders_page(user_id):
    rows = db.get_my_orders(user_id)
    parts = [header("<b>🧾 Pesanan Saya</b>")]
    if not rows:
        parts.append("Belum ada pesanan.\nGas belanja sekarang! 🛍️")
    for o in rows:
        icon = {
            "PENDING": "⏳",
            "PAID": "💳",
            "COMPLETED": "✓",
            "FAILED": "❌",
            "PAID_BUT_OUT_OF_STOCK": "⛔",
            "AWAITING_ADMIN": "🕐",
        }.get(o["status"], "❓")
        parts.append(
            f"{esc(o['product_name'])} × {o['qty']}\n"
            f"   <code>{o['order_id']}</code>\n"
            f"   {fmt_price(o['total'])} · {icon} {o['status']}"
        )
    text = "\n\n".join(parts)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Gas Belanja", callback_data="catalog")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def contact_page():
    admin = db.get_setting("ADMIN_USERNAME", "admin")
    text = (
        f"{header('<b>🎧 Bantuan</b>')}\n\n"
        f"Ada kendala saat berbelanja?\n"
        f"Hubungi admin kami: @{esc(admin)}\n\n"
        f"<b>Command tersedia</b>\n"
        f"/start — halaman utama\n"
        f"/products — jelajahi produk\n"
        f"/promo — lihat promo\n"
        f"/stock — cek stok\n"
        f"/orders — pesanan saya\n"
        f"/help — bantuan ini"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📩 Chat Admin", url=f"https://t.me/{admin}")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def loading_text(msg="Membuat pesananmu..."):
    return f"⏳ <b>{esc(msg)}</b>\n\nMohon tunggu sebentar...", InlineKeyboardMarkup([])


def nevapedia_page(order, invoice):
    total = invoice.get("total") or order["total"]
    fee = invoice.get("fee") or 0
    text = (
        f"{header('<b>💳 Pembayaran QRIS</b>')}\n\n"
        f"🛍️ {esc(order['product_name'])} × {order['qty']}\n"
        f"🧾 <code>{order['order_id']}</code>\n\n"
        f"💰 Transfer PERSIS: <b>{fmt_price(total)}</b>\n"
        f"   (termasuk biaya {fmt_price(fee)})\n\n"
        f"1️⃣ Scan/pay QRIS di atas\n"
        f"2️⃣ Setelah bayar, tekan <b>Cek Status Pembayaran</b> 👇\n\n"
        f"Produk dikirim otomatis begitu pembayaran terverifikasi. ✅"
    )
    buttons = []
    if invoice.get("payment_link"):
        buttons.append(
            [InlineKeyboardButton("💳 Bayar via Link", url=invoice["payment_link"])]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                "↻ Cek Status Pembayaran",
                callback_data=f"paid:{order['order_id']}",
            )
        ]
    )
    buttons.append([InlineKeyboardButton("⌂ Home", callback_data="home")])
    return text, InlineKeyboardMarkup(buttons)


def qris_static_page(order):
    text = (
        f"{header('<b>💳 Pembayaran QRIS</b>')}\n\n"
        f"🛍️ {esc(order['product_name'])} × {order['qty']}\n"
        f"🧾 <code>{order['order_id']}</code>\n\n"
        f"💰 Transfer PERSIS: <b>{fmt_price(order['total'])}</b>\n\n"
        f"1️⃣ Scan/pay QRIS di atas\n"
        f"2️⃣ Masukkan nominal <b>{fmt_price(order['total'])}</b>\n"
        f"3️⃣ Setelah bayar, tekan tombol <b>Konfirmasi Pembayaran</b> 👇\n\n"
        f"Produk dikirim setelah diverifikasi admin. ✅"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Saya Sudah Bayar",
                    callback_data=f"confirm_pay:{order['order_id']}",
                )
            ],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def test_payment_page(order):
    text = (
        f"{header('<b>🧪 Mode Uji Coba</b>')}\n\n"
        f"Pembayaran <b>disimulasikan</b> — tanpa uang asli.\n\n"
        f"🛍️ {esc(order['product_name'])} × {order['qty']}\n"
        f"💰 <b>{fmt_price(order['total'])}</b>\n"
        f"🧾 <code>{order['order_id']}</code>\n\n"
        f"Klik tombol di bawah untuk mensimulasikan pembayaran berhasil."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Bayar Sekarang (Simulasi)", callback_data=f"paid:{order['order_id']}")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def pending_page(order):
    text = (
        f"{header('<b>⏳ Menunggu Pembayaran</b>')}\n\n"
        f"🛍️ {esc(order['product_name'])} × {order['qty']}\n"
        f"💰 <b>{fmt_price(order['total'])}</b>\n"
        f"🧾 <code>{order['order_id']}</code>\n\n"
        f"Selesaikan pembayaran lalu cek status kembali."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↻ Cek Status", callback_data=f"paid:{order['order_id']}"
                )
            ],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def awaiting_admin_page(order_id):
    text = (
        f"{header('<b>🕐 Menunggu Verifikasi Admin</b>')}\n\n"
        f"🧾 <code>{esc(order_id)}</code>\n\n"
        f"Pembayaran kamu sudah tercatat dan sedang diperiksa admin.\n"
        f"Produk akan dikirim otomatis begitu disetujui. 🙏"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
    )
    return text, keyboard


def success_page(order_id):
    text = (
        f"{header('<b>✓ Pembayaran Berhasil</b>')}\n\n"
        f"✓ <b>Pembayaran terverifikasi!</b>\n"
        f"🧾 <code>{esc(order_id)}</code>\n\n"
        f"Produk digital sudah dikirim di pesan di atas. 🎁\n"
        f"Terima kasih sudah berbelanja!"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
    )
    return text, keyboard


def no_stock_paid_page(order_id):
    text = (
        f"{header('<b>⚠️ Pembayaran Diterima</b>')}\n\n"
        f"✓ <b>Pembayaranmu sudah kami terima.</b>\n"
        f"🧾 <code>{esc(order_id)}</code>\n\n"
        f"Namun saat ini stok produk sedang habis, jadi produk belum bisa dikirim.\n"
        f"Admin akan menghubungi kamu untuk pengembalian dana (refund).\n"
        f"Terima kasih atas kesabaranmu! 🙏"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
    )
    return text, keyboard


def error_page(message="Terjadi kesalahan, coba lagi nanti."):
    text = f"{header('<b>Oops</b>')}\n\n⚠️ {esc(message)}"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
    )
    return text, keyboard


def soldout_page():
    text = (
        f"{header('<b>⏳ Stok Habis</b>')}\n\n"
        f"Maaf, produk yang kamu pilih sudah habis.\n"
        f"Silakan pilih produk lain ya."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Lihat Produk", callback_data="catalog")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def admin_panel():
    products = db.get_active_products()
    total_stock = sum(db.count_available(p["id"]) for p in products)
    orders = db.get_all_orders(limit=50)
    pending = sum(1 for o in orders if o["status"] == "PENDING")
    completed = sum(1 for o in orders if o["status"] == "COMPLETED")

    text = (
        f"{header('<b>🔐 Admin Panel</b>')}\n\n"
        f"<b>Ringkasan</b>\n"
        f"   Produk aktif : {len(products)}\n"
        f"   Stok siap    : {total_stock}\n"
        f"   Pesanan      : {len(orders)} ({pending} menunggu, {completed} selesai)\n\n"
        f"Kelola data produk & stok langsung di Google Sheets."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 Stok", callback_data="stock"),
                InlineKeyboardButton("🧾 Pesanan", callback_data="ordersadmin"),
            ],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard


def admin_orders_page():
    rows = db.get_all_orders(limit=50)
    parts = [header("<b>🧾 Semua Pesanan</b>")]
    if not rows:
        parts.append("Belum ada pesanan.")
    for o in rows:
        icon = {
            "PENDING": "⏳",
            "PAID": "💳",
            "COMPLETED": "✓",
            "FAILED": "❌",
            "PAID_BUT_OUT_OF_STOCK": "⛔",
            "AWAITING_ADMIN": "🕐",
        }.get(o["status"], "❓")
        parts.append(
            f"{esc(o['product_name'])} × {o['qty']}\n"
            f"   <code>{o['order_id']}</code>\n"
            f"   {fmt_price(o['total'])} · {icon} {o['status']} · {o['telegram_id']}"
        )
    text = "\n\n".join(parts)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔐 Admin Panel", callback_data="admin")],
            [InlineKeyboardButton("⌂ Home", callback_data="home")],
        ]
    )
    return text, keyboard
