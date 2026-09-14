import asyncio
import io
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from PIL import Image
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest

import config
import db
import sync
import ui

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

_NEVAPEDIA_LOCK = threading.Lock()
_nevapedia_last_call = 0.0


def _nevapedia_throttle():
    global _nevapedia_last_call
    with _NEVAPEDIA_LOCK:
        elapsed = time.time() - _nevapedia_last_call
        if elapsed < 5.5:
            time.sleep(5.5 - elapsed)
        _nevapedia_last_call = time.time()


def nevapedia_create_invoice(order):
    try:
        _nevapedia_throttle()
        resp = requests.get(
            "https://app.nevapedia.com/api/invoice",
            params={
                "apikey": config.NEVAPEDIA_API_KEY,
                "amount": order["total"],
                "order_id": order["order_id"],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            logger.warning("Nevapedia create invoice gagal: %s", data)
            return None
        return data
    except Exception as e:
        logger.error("Nevapedia create invoice error: %s", e)
        return None


def nevapedia_get_status(invoice_id):
    if not invoice_id:
        return "pending"
    try:
        _nevapedia_throttle()
        resp = requests.get(
            "https://app.nevapedia.com/api/invoice/status",
            params={
                "apikey": config.NEVAPEDIA_API_KEY,
                "invoice_id": invoice_id,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("status", "pending")).lower()
    except Exception as e:
        logger.warning("Nevapedia status check error: %s", e)
        return "error"


def nevapedia_is_paid(status):
    return status in ("paid", "success", "completed", "settled")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    try:
        port = int(os.environ.get("PORT", "8000"))
        httpd = HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        logger.info("Health server on port %s", port)
    except Exception as e:
        logger.warning("Tidak bisa start health server: %s", e)


async def keep_alive(context: ContextTypes.DEFAULT_TYPE):
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    try:
        await asyncio.to_thread(requests.get, url, timeout=10)
    except Exception as e:
        logger.warning("Keep-alive ping gagal: %s", e)


def frame_qris_image(image_bytes, border_color=(20, 76, 249), border_ratio=0.06, corner_radius=24):
    """Lapisi gambar QRIS dengan bingkai tanpa mengubah isi QR.

    Isi QR (termasuk nominal) sudah ter-encode di dalam gambar asli oleh
    gateway — kita hanya menempelkannya di atas kanvas dengan border,
    sehingga kode QR tetap terbaca dan nominalnya tetap benar.
    """
    import io as _io

    raw = Image.open(_io.BytesIO(image_bytes)).convert("RGBA")
    raw_w, raw_h = raw.size
    pad = max(int(raw_w * border_ratio), 24)
    w = raw_w + pad * 2
    h = raw_h + pad * 2

    canvas = Image.new("RGBA", (w, h), border_color + (255,))
    mask = Image.new("L", (w, h), 0)
    from PIL import ImageDraw

    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=corner_radius, fill=255)
    canvas.paste(raw, (pad, pad))
    canvas.putalpha(mask)
    out = canvas.convert("RGB")

    buf = _io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def _sheet_write_url_ok(url):
    return bool(url) and url.startswith("https://script.google.com/macros/s/")


def _post_sheet(payload, retries=3):
    url = config.SHEET_WRITE_URL
    if not url:
        logger.warning(
            "SHEET_WRITE_URL kosong: write-back ke Google Sheets dilewati "
            "(order/stok tidak akan tercatat di sheet sampai env diisi). "
            "Cek env Render/SHEET_WRITE_URL."
        )
        return False
    if not _sheet_write_url_ok(url):
        logger.warning("SHEET_WRITE_URL tidak valid, dilewati: %s", url)
        return False
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                url,
                json={"secret": config.SHEET_WRITE_SECRET, **payload},
                timeout=15,
            )
            if resp.ok and "OK" in resp.text:
                return True
            last_err = f"HTTP {resp.status_code} — {resp.text[:120]}"
            logger.warning(
                "Write-back sheet tidak OK (attempt %s/%s): %s",
                attempt,
                retries,
                last_err,
            )
        except Exception as e:
            last_err = str(e)
            logger.warning(
                "Write-back sheet gagal (attempt %s/%s): %s", attempt, retries, e
            )
        time.sleep(1 * attempt)
    return False


def sync_sold_to_sheet(stock_ids, telegram_id):
    return _post_sheet(
        {
            "mark_sold": [
                {"stock_id": s, "sold_to": str(telegram_id)} for s in stock_ids
            ],
        }
    )


def sync_order_to_sheet(order):
    return _post_sheet(
        {
            "orders": [
                {
                    "order_id": order["order_id"],
                    "telegram_id": str(order["telegram_id"]),
                    "username": order.get("username") or "",
                    "product_id": order["product_id"],
                    "qty": order["qty"],
                    "total": order["total"],
                    "status": order["status"],
                    "payment_id": order.get("payment_id") or "",
                    "created_at": order.get("created_at") or "",
                    "paid_at": order.get("paid_at") or "",
                }
            ],
        }
    )


def get_product(product_id):
    products = db.get_active_products()
    return next((p for p in products if p["id"] == product_id), None)


async def send_page(chat_id, text, keyboard):
    await app.bot.send_message(
        chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard
    )


async def _send_fallback(chat_id, text, keyboard):
    try:
        await app.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard
        )
    except Exception as e2:
        logger.error("Kirim pesan pengganti gagal: %s", e2)


async def safe_edit(chat_id, message_id, text, reply_markup=None):
    try:
        await app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        logger.warning("Edit gagal, kirim pesan baru: %s", e)
        await _send_fallback(chat_id, text, reply_markup)
    except Exception as e:
        logger.warning("Edit gagal, kirim pesan baru: %s", e)
        await _send_fallback(chat_id, text, reply_markup)


async def render_home(chat_id, edit_message_id=None, user_name=None):
    text, kb = ui.home_text(user_name)
    if edit_message_id:
        await safe_edit(chat_id, edit_message_id, text, kb)
    elif config.BANNER_URL:
        try:
            await app.bot.send_photo(
                chat_id=chat_id,
                photo=config.BANNER_URL,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Kirim banner gagal, fallback teks: %s", e)
            await send_page(chat_id, text, kb)
    else:
        await send_page(chat_id, text, kb)


async def render_catalog(chat_id, edit_message_id=None):
    text, kb = ui.catalog_text()
    if edit_message_id:
        await safe_edit(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=text,
            reply_markup=kb,
        )
    else:
        await send_page(chat_id, text, kb)


async def render_product(chat_id, edit_message_id, product_id, qty):
    product = get_product(product_id)
    if not product:
        text, kb = ui.error_page("Produk tidak ditemukan.")
    else:
        avail = db.count_available(product_id)
        if avail < 1:
            text, kb = ui.soldout_page()
        else:
            if qty > avail:
                qty = avail
            text, kb = ui.product_page(product, qty)
    await safe_edit(
        chat_id=chat_id,
        message_id=edit_message_id,
        text=text,
        reply_markup=kb,
    )


async def check_member(bot, user_id):
    if not config.CHANNEL_USERNAME:
        return True
    try:
        member = await bot.get_chat_member(chat_id=config.CHANNEL_USERNAME, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("check_member gagal (fail-open): %s", e)
        return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if args:
        payload = args[0]
        if payload.startswith("ref_"):
            referrer = payload[4:]
            if referrer.isdigit() and int(referrer) != user_id:
                db.set_referred(str(user_id), referrer)
    is_member = await check_member(app.bot, user_id)
    if not is_member:
        text, kb = ui.force_join_page()
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    await asyncio.to_thread(sync.sync_from_sheets)
    name = update.effective_user.first_name or update.effective_user.username
    await render_home(update.effective_chat.id, user_name=name)


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_member = await check_member(app.bot, user_id)
    if not is_member:
        text, kb = ui.force_join_page()
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    await asyncio.to_thread(sync.sync_from_sheets)
    name = update.effective_user.first_name or update.effective_user.username
    await render_home(update.effective_chat.id, user_name=name)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = ui.contact_page()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(sync.sync_from_sheets)
    text, kb = ui.catalog_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def promo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(sync.sync_from_sheets)
    text, kb = ui.promo_page()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(sync.sync_from_sheets)
    text, kb = ui.stock_page()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = ui.orders_page(update.effective_user.id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def is_admin(user_id):
    return config.ADMIN_CHAT_ID is not None and user_id == config.ADMIN_CHAT_ID


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Akses khusus admin. 🙅")
        return
    text, kb = ui.admin_panel()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Akses khusus admin. 🙅")
        return
    await asyncio.to_thread(sync.sync_from_sheets)
    text, kb = ui.catalog_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Akses khusus admin. 🙅")
        return
    await asyncio.to_thread(sync.sync_from_sheets)
    text, kb = ui.stock_page()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Akses khusus admin. 🙅")
        return
    text, kb = ui.admin_orders_page()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    data = query.data

    if data == "checkjoin":
        is_member = await check_member(app.bot, query.from_user.id)
        if not is_member:
            await query.answer("Kamu belum join channel. Silakan join dulu ya!", show_alert=True)
            return
        await asyncio.to_thread(sync.sync_from_sheets)
        name = query.from_user.first_name or query.from_user.username
        await render_home(chat_id, msg_id, user_name=name)

    elif data == "home":
        is_member = await check_member(app.bot, query.from_user.id)
        if not is_member:
            await query.answer()
            text, kb = ui.force_join_page()
            await safe_edit(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
            )
            return
        await asyncio.to_thread(sync.sync_from_sheets)
        name = query.from_user.first_name or query.from_user.username
        await render_home(chat_id, msg_id, user_name=name)

    elif data == "refresh":
        is_member = await check_member(app.bot, query.from_user.id)
        if not is_member:
            await query.answer()
            text, kb = ui.force_join_page()
            await safe_edit(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
            )
            return
        await asyncio.to_thread(sync.sync_from_sheets)
        name = query.from_user.first_name or query.from_user.username
        await render_home(chat_id, msg_id, user_name=name)

    elif data == "promo":
        await asyncio.to_thread(sync.sync_from_sheets)
        text, kb = ui.promo_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )

    elif data == "catalog":
        await asyncio.to_thread(sync.sync_from_sheets)
        await render_catalog(chat_id, msg_id)

    elif data == "stock":
        await asyncio.to_thread(sync.sync_from_sheets)
        text, kb = ui.stock_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )

    elif data == "orders":
        text, kb = ui.orders_page(query.from_user.id)
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )

    elif data == "contact":
        text, kb = ui.contact_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )

    elif data == "affiliate":
        text = affiliate_text(str(query.from_user.id))
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Home", callback_data="home")]]
        )
        await safe_edit(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)

    elif data == "admin":
        if not is_admin(query.from_user.id):
            await query.answer("Akses khusus admin.")
            return
        text, kb = ui.admin_panel()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )

    elif data == "ordersadmin":
        if not is_admin(query.from_user.id):
            await query.answer("Akses khusus admin.")
            return
        text, kb = ui.admin_orders_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )

    elif data.startswith("product:"):
        product_id = data.split(":", 1)[1]
        context.user_data["qty"] = 1
        context.user_data["product_id"] = product_id
        await render_product(chat_id, msg_id, product_id, 1)

    elif data.startswith("qtydec:") or data.startswith("qtyinc:"):
        op, product_id = data.split(":", 1)
        qty = context.user_data.get("qty", 1)
        if op == "qtydec":
            qty = max(1, qty - 1)
        else:
            avail = db.count_available(product_id)
            qty = max(1, min(avail, qty + 1))
        context.user_data["qty"] = qty
        context.user_data["product_id"] = product_id
        await render_product(chat_id, msg_id, product_id, qty)

    elif data.startswith("buy:"):
        product_id = data.split(":", 1)[1]
        qty = context.user_data.get("qty", 1)
        if not isinstance(qty, int) or qty < 1:
            qty = 1
        context.user_data["product_id"] = product_id
        context.user_data["qty"] = qty
        await do_checkout(query, context, chat_id, msg_id)

    elif data.startswith("paid:"):
        await paid_check(query, context, chat_id, msg_id)

    elif data.startswith("confirm_pay:"):
        order_id = data.split(":", 1)[1]
        await confirm_payment(query, context, order_id)

    elif data.startswith("admin_approve:"):
        order_id = data.split(":", 1)[1]
        await admin_approve(query, context, chat_id, msg_id, order_id)

    elif data.startswith("admin_reject:"):
        order_id = data.split(":", 1)[1]
        await admin_reject(query, context, chat_id, msg_id, order_id)

    elif data == "noop":
        pass

    await query.answer()


async def do_checkout(query, context, chat_id, msg_id):
    product_id = context.user_data.get("product_id")
    qty = context.user_data.get("qty", 1)
    if not isinstance(qty, int) or qty < 1:
        qty = 1
    product = get_product(product_id)
    if not product:
        text, kb = ui.error_page("Produk tidak ditemukan.")
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )
        return
    if db.count_available(product_id) < qty:
        text, kb = ui.soldout_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )
        return

    text, kb = ui.loading_text("Membuat pesananmu...")
    await safe_edit(
        chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
    )

    total = product["price"] * qty
    if db.count_available(product_id) < qty:
        text, kb = ui.soldout_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )
        return
    order_id = "ORD-" + uuid.uuid4().hex[:10].upper()
    user = query.from_user
    db.create_order(order_id, user.id, user.username or "", product, qty, total)

    order = db.get_order(order_id)
    await asyncio.to_thread(sync_order_to_sheet, order)

    if config.TEST_MODE:
        text, kb = ui.test_payment_page(order)
        await safe_edit(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            reply_markup=kb,
        )
        await notify_admin(
            f"🔔 <b>PESANAN BARU (TEST)</b>\n"
            f"🆔 Order: <code>{order_id}</code>\n"
            f"{product['emoji']} {ui.esc(product['name'])} x{qty}\n"
            f"💰 Total: <b>Rp{total:,}</b>\n"
            f"👤 User: @{user.username or '-'} ({user.id})"
        )
        return

    if config.PAYMENT_METHOD == "nevapedia":
        if not config.NEVAPEDIA_API_KEY:
            db.set_order_status(order_id, "FAILED")
            await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
            text, kb = ui.error_page("Payment gateway belum dikonfigurasi.")
            await safe_edit(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
            )
            return
        if not db.reserve_stock(order_id, product_id, qty):
            db.set_order_status(order_id, "FAILED")
            await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
            text, kb = ui.soldout_page()
            await safe_edit(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
            )
            return
        try:
            invoice = await asyncio.to_thread(nevapedia_create_invoice, order)
            if not invoice:
                db.release_reservation(order_id)
                db.set_order_status(order_id, "FAILED")
                await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
                text, kb = ui.error_page(
                    "Gagal membuat pembayaran. Admin akan menghubungi kamu."
                )
                await safe_edit(
                    chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
                )
                await notify_admin(
                    f"⚠️ <b>GAGAL BUAT INVOICE NEVAPEDIA</b>\n"
                    f"🆔 Order: <code>{order_id}</code>\n"
                    f"{ui.esc(product['name'])} x{qty}\n"
                    f"💰 Total: Rp{total}\n"
                    f"👤 User: {user.id}"
                )
                return
            db.update_payment_id(order_id, invoice.get("invoice_id"))
            text, kb = ui.nevapedia_page(order, invoice)
            qris_image = invoice.get("qris_image")
            if qris_image and qris_image.startswith("https://"):
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
                try:
                    qris_bytes = await asyncio.to_thread(
                        lambda: requests.get(qris_image, timeout=20).content
                    )
                    framed = await asyncio.to_thread(frame_qris_image, qris_bytes)
                except Exception as e:
                    logger.warning("Gagal mem-frame QRIS, kirim asli: %s", e)
                    framed = None
                photo = framed if framed else qris_image
                await app.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            else:
                await safe_edit(
                    chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
                )
            await notify_admin(
                f"🔔 <b>PESANAN BARU (NEVAPEDIA)</b>\n\n"
                f"🆔 Order: <code>{order_id}</code>\n"
                f"{product['emoji']} {ui.esc(product['name'])} x{qty}\n"
                f"💰 Total: <b>Rp{invoice.get('total', total):,}</b>\n"
                f"👤 User: @{user.username or '-'} ({user.id})"
            )
        except Exception as e:
            logger.error("Nevapedia payment error: %s", e)
            db.release_reservation(order_id)
            db.set_order_status(order_id, "FAILED")
            await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
            text, kb = ui.error_page("Gagal membuat pembayaran. Admin akan menghubungi kamu.")
            try:
                await safe_edit(
                    chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
                )
            except Exception:
                await app.bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=kb
                )
            await notify_admin(
                f"⚠️ <b>GAGAL BUAT PAYMENT</b>\n"
                f"🆔 Order: <code>{order_id}</code>\n"
                f"{ui.esc(product['name'])} x{qty}\n"
                f"💰 Total: Rp{total}\n"
                f"👤 User: {user.id}\n"
                f"Error: {e}"
            )
        return

    if not db.reserve_stock(order_id, product_id, qty):
        db.set_order_status(order_id, "FAILED")
        await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
        text, kb = ui.soldout_page()
        await safe_edit(
            chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
        )
        return

    try:
        text, kb = ui.qris_static_page(order)
        if config.QRIS_IMAGE_URL:
            try:
                await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
            await app.bot.send_photo(
                chat_id=chat_id,
                photo=config.QRIS_IMAGE_URL,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            await safe_edit(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
            )
        await notify_admin(
            f"🔔 <b>PESANAN BARU (QRIS)</b>\n\n"
            f"🆔 Order: <code>{order_id}</code>\n"
            f"{product['emoji']} {ui.esc(product['name'])} x{qty}\n"
            f"💰 Total: <b>Rp{total:,}</b>\n"
            f"👤 User: @{user.username or '-'} ({user.id})"
        )
    except Exception as e:
        logger.error("QRIS payment error: %s", e)
        db.release_reservation(order_id)
        db.set_order_status(order_id, "FAILED")
        await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
        text, kb = ui.error_page("Gagal membuat pembayaran. Admin akan menghubungi kamu.")
        try:
            await safe_edit(
                chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb
            )
        except Exception:
            await app.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=kb
            )
        await notify_admin(
            f"⚠️ <b>GAGAL BUAT PAYMENT</b>\n"
            f"🆔 Order: <code>{order_id}</code>\n"
            f"{ui.esc(product['name'])} x{qty}\n"
            f"💰 Total: Rp{total}\n"
            f"👤 User: {user.id}\n"
            f"Error: {e}"
        )


async def paid_check(query, context, chat_id, msg_id):
    order_id = query.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        text, kb = ui.error_page("Order tidak ditemukan.")
        await safe_edit(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)
        return
    if str(order["telegram_id"]) != str(query.from_user.id):
        text, kb = ui.error_page("Order ini bukan milik kamu ya.")
        await safe_edit(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)
        return

    if config.TEST_MODE and order["status"] not in (
        "COMPLETED", "PAID_BUT_OUT_OF_STOCK", "FAILED"
    ):
        await complete_order(order_id, "SIMULATED", context)
        order = db.get_order(order_id)

    elif order["status"] in ("PENDING", "FAILED") and config.PAYMENT_METHOD == "nevapedia":
        if not config.NEVAPEDIA_API_KEY:
            text, kb = ui.error_page("Payment gateway belum dikonfigurasi.")
            await safe_edit(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)
            return
        try:
            status = await asyncio.to_thread(nevapedia_get_status, order.get("payment_id"))
            if nevapedia_is_paid(status):
                await complete_order(
                    order_id, order.get("payment_id") or order_id, context
                )
            elif status in ("canceled", "expired", "failed") and order["status"] == "PENDING":
                db.release_reservation(order_id)
                db.set_order_status(order_id, "FAILED")
                await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
        except Exception as e:
            logger.error("Status check error: %s", e)
            text, kb = ui.error_page("Gagal cek status. Coba lagi nanti.")
            await safe_edit(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)
            return
        order = db.get_order(order_id)

    if not order:
        text, kb = ui.error_page("Order tidak ditemukan.")
    elif order["status"] == "COMPLETED":
        if not order.get("delivered"):
            contents = db.get_stock_contents_by_order(order_id)
            if contents:
                if await send_product_file(context, order, contents):
                    db.set_order_delivered(order_id)
        text, kb = ui.success_page(order_id)
    elif order["status"] in ("NO_STOCK", "PAID_BUT_OUT_OF_STOCK"):
        text, kb = ui.no_stock_paid_page(order_id)
    elif order["status"] == "AWAITING_ADMIN":
        text, kb = ui.awaiting_admin_page(order_id)
    elif order["status"] == "FAILED":
        text, kb = ui.error_page("Pembayaran gagal atau dibatalkan.")
    else:
        text, kb = ui.pending_page(order)
    try:
        await safe_edit(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb)
    except Exception:
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
        await app.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=kb
        )


GENERAL_TERMS = (
    "== KETENTUAN & CARA PEMAKAIAN ==\n\n"
    "> Simpan file ini dengan aman dan jangan bagikan kepada siapa pun.\n"
    "> Baca petunjuk aktivasi produk dengan teliti sebelum memulai.\n"
    "> Pastikan akun tujuan sudah sesuai dengan ketentuan produk.\n"
    "> Garansi hanya berlaku sesuai ketentuan pada tiap produk.\n\n"
)

GOOGLE_AI_PRO_TERMS = (
    "📬 BACA SEBELUM AKTIVASI:\n"
    "- Periksa akun email tujuan terlebih dahulu sebelum menekan tombol \"Aktivasi\".\n"
    "- Hindari aktivasi pada akun email yang telah memiliki langganan aktif Google Plus, Pro, atau Ultra.\n"
    "- Pastikan Anda telah masuk ke akun email yang dituju saat proses aktivasi. Akun yang sedang aktif dapat diperiksa pada pojok kanan atas layar.\n\n"
    "🛡️ KETENTUAN & GARANSI:\n"
    "> Tersedia garansi 6 jam untuk memastikan proses aktivasi dan link berjalan dengan baik.\n"
    "> Langganan akan langsung aktif pada akun Anda setelah aktivasi berhasil.\n"
    "> Tidak tersedia garansi toko setelah proses aktivasi selesai.\n"
    "> Garansi hanya berlaku untuk proses aktivasi.\n\n"
    "🆘 PEMBERITAHUAN PENTING:\n"
    "> Karena paket ini dikelola oleh Jio, langganan Google AI Pro akan berakhir jika paket SIM Jio menjadi tidak aktif. "
    "Inilah alasan mengapa produk ini memiliki garansi nol (tidak ada garansi) setelah aktivasi. "
    "Jika beruntung, Anda mungkin dapat menggunakannya hingga 18 bulan. "
    "Namun, jika paket SIM Jio diubah atau menjadi tidak aktif, langganan Google AI Pro juga akan berakhir.\n\n"
    "⚠️ Catatan:\n"
    "> Kode redeem ini hanya dapat digunakan 1 kali untuk setiap akun.\n"
)


async def send_product_file(context, order, contents):
    file_text = (
        f"PEMBAYARAN BERHASIL\n"
        f"Order: {order['order_id']}\n"
        f"Produk: {order['product_name']} x{order['qty']}\n"
        f"Total: Rp{order['total']:,}\n"
        f"Waktu: {db.utcnow().isoformat()}\n\n"
        f"== PRODUK DIGITAL ==\n\n"
    )
    for i, content in enumerate(contents, 1):
        file_text += f"Produk {i}:\n{content}\n\n"
    file_text += GENERAL_TERMS
    if "google ai pro 18 bulan" in order["product_name"].lower():
        file_text += GOOGLE_AI_PRO_TERMS
    file_text += "Terima kasih sudah berbelanja!\n"
    buf = io.BytesIO(file_text.encode("utf-8"))
    buf.name = f"produk-{order['order_id']}.txt"
    await context.bot.send_document(
        chat_id=order["telegram_id"],
        document=InputFile(buf),
        caption=f"📂 Produk digital kamu — Order <code>{order['order_id']}</code>",
        parse_mode="HTML",
    )


async def complete_order(order_id, payment_id, context):
    result = await asyncio.to_thread(
        db.complete_order_atomic, order_id, payment_id or order_id
    )
    status = result["status"]
    if status in ("ALREADY", "NOT_FOUND"):
        return status

    order = result["order"]
    contents = result["contents"]
    stock_ids = result["stock_ids"]

    order_sheet = db.get_order(order_id)
    if order_sheet:
        await asyncio.to_thread(sync_order_to_sheet, order_sheet)

    if status == "PAID_BUT_OUT_OF_STOCK":
        await notify_admin(
            f"⚠️ <b>ORDER OUT OF STOCK</b>\n"
            f"Order <code>{order_id}</code> dibayar tapi stok habis (sudah diklaim user lain). "
            f"Refund/manual resolution diperlukan.\n"
            f"👤 User: {order['telegram_id']}"
        )
        return status

    sold_ok = await asyncio.to_thread(
        sync_sold_to_sheet, stock_ids, order["telegram_id"]
    )
    if not sold_ok:
        await notify_admin(
            f"🚨 <b>WRITE-BACK STOK GAGAL</b>\n\n"
            f"Stok order <code>{order_id}</code> (ids: {', '.join(stock_ids)}) sudah "
            f"terjual di DB lokal tapi GAGAL disinkronkan ke Google Sheets.\n\n"
            f"⚠️ Jika server diredeploy sebelum stok ini di-sync manual ke sheet, "
            f"kredensial bisa dijual 2x. Harap cek & perbaiki sheet STOCK segera.\n"
            f"👤 User: {order['telegram_id']}"
        )

    if not config.TEST_MODE:
        referrer = db.get_referrer(str(order["telegram_id"]))
        if referrer and referrer != str(order["telegram_id"]):
            commission = int(round(order["total"] * config.AFFILIATE_PERCENT / 100))
            if commission > 0:
                db.credit_commission(
                    referrer,
                    str(order["telegram_id"]),
                    order_id,
                    order["product_name"],
                    commission,
                )
                await notify_affiliate(referrer, commission, order_id)

    intro = (
        f"✓ <b>Pembayaran Berhasil!</b>\n\n"
        + ("🧪 <i>Mode uji coba — pembayaran disimulasikan.</i>\n\n" if config.TEST_MODE else "")
        + f"🛍️ {ui.esc(order['product_name'])} x{order['qty']}\n"
        f"🧾 <code>{order_id}</code>\n\n"
    )

    try:
        await context.bot.send_message(
            chat_id=order["telegram_id"],
            text=intro + "Silakan ambil akses produk kamu di file terlampir 👇",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Kirim intro produk gagal: %s", e)
    delivered = False
    try:
        await send_product_file(context, order, contents)
        delivered = True
        db.set_order_delivered(order_id)
    except Exception as e:
        logger.error("Kirim file produk gagal: %s", e)
    await notify_admin(
        f"✅ <b>PEMBAYARAN BERHASIL</b>\n\n"
        f"🆔 Order <code>{order_id}</code>\n"
        f"🛒 {ui.esc(order['product_name'])} x{order['qty']}\n"
        f"💰 Total: <b>Rp{order['total']:,}</b>\n"
        f"👤 User: {order['telegram_id']}\n"
        f"📦 Status: <b>COMPLETED</b>{' ⚠️ file GAGAL dikirim' if not delivered else ''}"
    )
    await notify_channel(
        f"✅ <b>PENJUALAN BARU</b>\n\n"
        f"🛒 {ui.esc(order['product_name'])} x{order['qty']}\n"
        f"💰 Total: <b>Rp{order['total']:,}</b>\n"
        f"📦 Status: <b>COMPLETED</b>"
    )
    return "COMPLETED"


async def confirm_payment(query, context, order_id):
    order = db.get_order(order_id)
    if not order or str(order["telegram_id"]) != str(query.from_user.id):
        await query.answer("Order tidak ditemukan.")
        return
    if order["status"] != "PENDING":
        await query.answer("Status pesanan sudah berubah.")
        return
    if config.PAYMENT_METHOD == "nevapedia" or order.get("payment_id"):
        await query.answer("Order ini tidak memakai verifikasi manual.")
        return
    db.set_order_status(order_id, "AWAITING_ADMIN")
    await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⌂ Home", callback_data="home")]]
    )
    caption = (
        f"⏳ <b>Pembayaran Diverifikasi</b>\n\n"
        f"Terima kasih! Pembayaran kamu sedang diperiksa admin.\n"
        f"Produk dikirim otomatis setelah disetujui. 🙏"
    )
    try:
        await query.edit_message_caption(caption=caption, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("Edit caption confirm gagal: %s", e)
        await query.message.reply_text(caption, parse_mode="HTML", reply_markup=kb)
    await query.answer("Terima kasih! ✅")
    await notify_admin_pending_verification(order_id)


async def notify_admin_pending_verification(order_id):
    if config.ADMIN_CHAT_ID is None:
        return
    order = db.get_order(order_id)
    if not order:
        return
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{order_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{order_id}"),
            ]
        ]
    )
    text = (
        f"🕐 <b>PEMBAYARAN MENUNGGU VERIFIKASI</b>\n\n"
        f"🆔 Order: <code>{order_id}</code>\n"
        f"🛒 {ui.esc(order['product_name'])} x{order['qty']}\n"
        f"💰 Total: <b>Rp{order['total']:,}</b>\n"
        f"👤 User: {order['telegram_id']}\n\n"
        f"Cek saldo yang masuk, lalu <b>Approve</b> atau <b>Reject</b>."
    )
    try:
        await app.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as e:
        logger.error("Notif verifikasi admin gagal: %s", e)


async def admin_approve(query, context, chat_id, msg_id, order_id):
    if config.ADMIN_CHAT_ID is None or str(query.from_user.id) != str(config.ADMIN_CHAT_ID):
        await query.answer("Hanya admin yang bisa approve.")
        return
    order = db.get_order(order_id)
    if not order:
        await query.answer("Order tidak ditemukan.")
        return
    if order["status"] != "AWAITING_ADMIN":
        await query.answer("Status order bukan menunggu verifikasi.")
        return
    await complete_order(order_id, order_id, context)
    await query.answer("Disetujui.")
    try:
        await safe_edit(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"✅ <b>Order disetujui.</b>\nProduk untuk <code>{order_id}</code> sudah dikirim.",
            reply_markup=InlineKeyboardMarkup([]),
        )
    except Exception:
        pass


async def admin_reject(query, context, chat_id, msg_id, order_id):
    if config.ADMIN_CHAT_ID is None or str(query.from_user.id) != str(config.ADMIN_CHAT_ID):
        await query.answer("Hanya admin yang bisa reject.")
        return
    order = db.get_order(order_id)
    if not order:
        await query.answer("Order tidak ditemukan.")
        return
    if order["status"] != "AWAITING_ADMIN":
        await query.answer("Status order bukan menunggu verifikasi.")
        return
    db.release_reservation(order_id)
    db.set_order_status(order_id, "FAILED")
    await asyncio.to_thread(sync_order_to_sheet, db.get_order(order_id))
    try:
        await context.bot.send_message(
            chat_id=order["telegram_id"],
            text="❌ Pembayaran kamu tidak terverifikasi. Jika sudah transfer, hubungi admin.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Notif reject ke user gagal: %s", e)
    await query.answer("Ditolak.")
    try:
        await safe_edit(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"❌ <b>Order ditolak.</b>\n<code>{order_id}</code> dibatalkan.",
            reply_markup=InlineKeyboardMarkup([]),
        )
    except Exception:
        pass


async def cleanup_reservations(context: ContextTypes.DEFAULT_TYPE):
    """Rutin lepas reservasi stok yang ditinggalkan (semua metode bayar)."""
    try:
        db.release_expired_reservations(max_age_hours=24)
    except Exception as e:
        logger.error("Cleanup reservasi gagal: %s", e)


async def check_payments(context: ContextTypes.DEFAULT_TYPE):
    if config.TEST_MODE or config.PAYMENT_METHOD != "nevapedia":
        return
    if not config.NEVAPEDIA_API_KEY:
        return
    now = db.utcnow()
    for order in db.get_pending_orders():
        try:
            created_at = order.get("created_at") or ""
            try:
                created_dt = datetime.fromisoformat(created_at)
            except ValueError:
                created_dt = None
            if created_dt and (now - created_dt).total_seconds() > 48 * 3600:
                db.release_reservation(order["order_id"])
                db.set_order_status(order["order_id"], "FAILED")
                await asyncio.to_thread(
                    sync_order_to_sheet, db.get_order(order["order_id"])
                )
                continue
            status = await asyncio.to_thread(nevapedia_get_status, order.get("payment_id"))
            paid = nevapedia_is_paid(status)
            if paid:
                await complete_order(
                    order["order_id"], order.get("payment_id") or order["order_id"], context
                )
            elif status in ("canceled", "expired", "failed"):
                db.release_reservation(order["order_id"])
                db.set_order_status(order["order_id"], "FAILED")
                await asyncio.to_thread(
                    sync_order_to_sheet, db.get_order(order["order_id"])
                )
        except Exception as e:
            logger.error("Polling error %s: %s", order["order_id"], e)


async def notify_affiliate(referrer_uid, amount, order_id):
    text = (
        f"🎉 <b>Komisi Masuk!</b>\n\n"
        f"Referralmu melakukan pembelian.\n"
        f"🧾 Order: <code>{order_id}</code>\n"
        f"💰 Komisi: <b>Rp{amount:,}</b>\n\n"
        f"Cek saldo dengan /affiliate"
    )
    try:
        await app.bot.send_message(
            chat_id=int(referrer_uid), text=text, parse_mode="HTML"
        )
    except Exception as e:
        logger.error("Notif komisi gagal: %s", e)


def affiliate_text(uid):
    link = (
        f"https://t.me/{bot_username}?start=ref_{uid}"
        if bot_username
        else "BOT_USERNAME belum terdeteksi"
    )
    balance = db.get_wallet(uid)
    refs = db.count_referrals(uid)
    text = (
        f"🤝 <b>PROGRAM AFILIASI</b>\n\n"
        f"Bagikan link di bawah ini. Kamu dapat komisi "
        f"<b>{config.AFFILIATE_PERCENT}%</b> dari setiap pembelian "
        f"berhasil lewat linkmu!\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"📊 Referral: <b>{refs}</b> orang\n"
        f"💰 Saldo komisi: <b>Rp{balance:,}</b>\n\n"
        f"Pencairan manual — hubungi admin ya."
    )
    comms = db.get_commissions(uid, 5)
    if comms:
        text += "\n\n🧾 <b>Komisi terakhir:</b>\n"
        for c in comms:
            status = "✅ dicairkan" if c["status"] == "PAID" else "⏳ pending"
            text += f"• <code>{c['order_id']}</code> Rp{c['amount']:,} · {status}\n"
    return text


async def affiliate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        affiliate_text(str(update.effective_user.id)), parse_mode="HTML"
    )


async def admin_affiliates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if config.ADMIN_CHAT_ID is None or str(update.effective_user.id) != str(config.ADMIN_CHAT_ID):
        await update.message.reply_text("Khusus admin.")
        return
    rows = db.get_all_wallets()
    if not rows:
        await update.message.reply_text("Belum ada komisi tercatat.")
        return
    text = "🤝 <b>DAFTAR REFERRER & KOMISI</b>\n\n"
    for i, w in enumerate(rows, 1):
        text += (
            f"{i}. UID <code>{w['uid']}</code>\n"
            f"   👥 {w['referrals']} ref · 🧾 {w['total_comm']} komisi · "
            f"💰 <b>Rp{w['balance']:,}</b>\n"
        )
    text += "\nCairkan: /payout &lt;uid&gt;"
    await update.message.reply_text(text, parse_mode="HTML")


async def admin_payout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if config.ADMIN_CHAT_ID is None or str(update.effective_user.id) != str(config.ADMIN_CHAT_ID):
        await update.message.reply_text("Khusus admin.")
        return
    if not context.args:
        await update.message.reply_text("Format: /payout <uid>")
        return
    uid = context.args[0]
    if not uid.isdigit():
        await update.message.reply_text("UID harus berupa angka.")
        return
    n = db.mark_payout(uid)
    await update.message.reply_text(f"✅ {n} komisi UID <code>{ui.esc(uid)}</code> ditandai PAID.", parse_mode="HTML")
    if n and uid.isdigit():
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text="🎉 Komisi kamu sudah dicairkan oleh admin. Terima kasih! 💵",
            )
        except Exception as e:
            logger.error("Notif payout gagal: %s", e)


async def notify_admin(text):
    if not config.ADMIN_CHAT_ID:
        return
    if config.TEST_MODE:
        text = f"🧪 <b>MODE UJI COBA</b>\n\n{text}"
    try:
        await app.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID, text=text, parse_mode="HTML"
        )
    except Exception as e:
        logger.error("Notifikasi admin gagal: %s", e)


async def notify_channel(text):
    if not config.CHANNEL_USERNAME:
        return
    try:
        await app.bot.send_message(
            chat_id=config.CHANNEL_USERNAME, text=text, parse_mode="HTML"
        )
    except Exception as e:
        logger.error("Notifikasi channel gagal: %s", e)


async def setup_commands(application):
    user_cmds = [
        BotCommand("start", "Mulai Bot"),
        BotCommand("stock", "Cek Stok"),
        BotCommand("promo", "Promo"),
        BotCommand("join", "Join Channel"),
        BotCommand("support", "Support"),
        BotCommand("affiliate", "Program Afiliasi"),
    ]
    admin_cmds = user_cmds + [
        BotCommand("admin", "Dashboard admin"),
        BotCommand("affiliates", "Daftar komisi"),
        BotCommand("payout", "Cairkan komisi"),
    ]
    try:
        await application.bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
        if config.ADMIN_CHAT_ID is not None:
            await application.bot.set_my_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=config.ADMIN_CHAT_ID)
            )
        logger.info("Command menu terpasang")
    except Exception as e:
        logger.error("Gagal setMyCommands: %s", e)


async def post_init(application):
    global bot_username
    try:
        me = await application.bot.get_me()
        bot_username = me.username or ""
    except Exception as e:
        logger.error("Gagal get_me untuk link afiliasi: %s", e)
    await setup_commands(application)


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel = (config.CHANNEL_USERNAME or "").strip().lstrip("@")
    if not channel:
        await update.message.reply_text("Channel belum dikonfigurasi.")
        return
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{channel}")]]
    )
    await update.message.reply_text(
        "Gabung dulu di channel kami untuk update promo & info produk terbaru 👇",
        reply_markup=kb,
    )


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛟 <b>Butuh Bantuan?</b>\n\n"
        "Untuk pertanyaan, kendala pembayaran, atau bantuan produk, silakan hubungi admin kami. "
        "Kami siap membantu! 🙏"
    )
    buttons = []
    if config.CHANNEL_USERNAME:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Join Channel", url=f"https://t.me/{config.CHANNEL_USERNAME.lstrip('@')}"
                )
            ]
        )
    admin_user = db.get_setting("ADMIN_USERNAME", "").strip().lstrip("@")
    if admin_user:
        buttons.append(
            [InlineKeyboardButton("💬 Chat Admin", url=f"https://t.me/{admin_user}")]
        )
    elif config.ADMIN_CHAT_ID is not None:
        buttons.append(
            [InlineKeyboardButton("💬 Chat Admin", url=f"tg://user?id={config.ADMIN_CHAT_ID}")]
        )
    if buttons:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await update.message.reply_text(text, parse_mode="HTML")


async def any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Gunakan tombol menu atau ketik /menu. 🙂")


app = None
bot_username = ""


def main():
    global app
    if not config.TOKEN:
        logger.error(
            "TOKEN belum diset. Isi file .env (lihat .env.example) atau set env TOKEN."
        )
        return
    if config.ADMIN_CHAT_ID is None:
        logger.warning(
            "ADMIN_CHAT_ID belum diset. Fitur admin akan nonaktif."
        )
    db.init_db()
    sync.sync_from_sheets()
    db.release_expired_reservations(max_age_hours=24)
    sync.restore_inflight_orders()
    start_health_server()

    app = (
        Application.builder()
        .token(config.TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("products", products_cmd))
    app.add_handler(CommandHandler("promo", promo_cmd))
    app.add_handler(CommandHandler("stock", stock_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("join", join_cmd))
    app.add_handler(CommandHandler("support", support_cmd))
    app.add_handler(CommandHandler("affiliate", affiliate_cmd))
    app.add_handler(CommandHandler("affiliates", admin_affiliates_cmd))
    app.add_handler(CommandHandler("payout", admin_payout_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("productsadmin", admin_products_cmd))
    app.add_handler(CommandHandler("stockadmin", admin_stock_cmd))
    app.add_handler(CommandHandler("ordersadmin", admin_orders_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, any_text))

    app.job_queue.run_repeating(check_payments, interval=60, first=30)
    app.job_queue.run_repeating(cleanup_reservations, interval=60, first=45)
    app.job_queue.run_repeating(keep_alive, interval=300, first=60)

    logger.info("Bot Wizual Shop berjalan (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
