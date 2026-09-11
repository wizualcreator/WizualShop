import csv
import io
import logging
import re
import threading

import requests

import config
import db

logger = logging.getLogger(__name__)

SYNC_LOCK = threading.Lock()


def fetch_csv(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    r.encoding = "utf-8"
    return list(csv.DictReader(io.StringIO(r.text)))


def _parse_price(raw):
    s = re.sub(r"[^\d]", "", str(raw or ""))
    try:
        return int(s) if s else 0
    except ValueError:
        return 0


def restore_inflight_orders():
    """Import ulang order yang masih berjalan dari sheet ORDERS.

    Berguna saat disk ephemeral (SQLite) terhapus oleh redeploy: order
    PENDING/AWAITING_ADMIN/PAID_BUT_OUT_OF_STOCK dipulihkan dari sheet
    sehingga admin masih bisa approve dan user masih bisa cek status.
    """
    try:
        rows = fetch_csv(config.ORDERS_URL)
    except Exception as e:
        logger.error("Gagal baca ORDERS sheet: %s", e)
        return
    restored = 0
    for row in rows:
        oid = str(row.get("ORDER_ID", "")).strip()
        status = str(row.get("STATUS", "")).strip().upper()
        if not oid:
            continue
        if status not in ("PENDING", "AWAITING_ADMIN", "PAID_BUT_OUT_OF_STOCK"):
            continue
        if db.get_order(oid):
            continue
        pid = str(row.get("PRODUCT_ID", "")).strip()
        pname = str(row.get("PRODUCT_NAME", "")).strip() or pid
        try:
            prod = next(
                (p for p in db.get_active_products() if p["id"] == pid), None
            )
            if prod:
                pname = prod["name"]
        except Exception:
            pass
        db.create_order(
            oid,
            str(row.get("TELEGRAM_ID", "")).strip(),
            str(row.get("USERNAME", "")).strip(),
            {
                "id": pid,
                "name": pname,
                "emoji": "",
                "price": _parse_price(row.get("TOTAL", "")),
                "status": "ACTIVE",
                "description": "",
            },
            max(1, _parse_price(row.get("QTY", ""))),
            _parse_price(row.get("TOTAL", "")),
        )
        db.set_order_status(oid, status, payment_id=row.get("PAYMENT_ID", "") or None, paid_at=row.get("PAID_AT", "") or None)
        restored += 1
    if restored:
        logger.info("Order dipulihkan dari sheet: %s", restored)


def sync_from_sheets():
    with SYNC_LOCK:
        try:
            for row in fetch_csv(config.PRODUCTS_URL):
                if not row.get("ID"):
                    continue
                db.upsert_product(
                    {
                        "id": str(row["ID"]).strip(),
                        "name": row.get("NAME", ""),
                        "emoji": row.get("EMOJI", ""),
                        "price": _parse_price(row.get("PRICE")),
                        "status": str(row.get("STATUS", "")).strip().upper(),
                        "description": row.get("DESCRIPTION", ""),
                    }
                )
            stock_rows = fetch_csv(config.STOCK_URL)
            for row in stock_rows:
                if not row.get("STOCK_ID"):
                    continue
                db.upsert_stock_row(
                    {
                        "stock_id": str(row["STOCK_ID"]).strip(),
                        "product_id": str(row.get("PRODUCT_ID", "")).strip(),
                        "content": row.get("CONTENT", ""),
                        "status": str(row.get("STATUS", "")).strip().upper(),
                        "sold_to": row.get("SOLD_TO", ""),
                    }
                )
            kept = {
                str(row["STOCK_ID"]).strip()
                for row in stock_rows
                if row.get("STOCK_ID")
            }
            available = db.count_all_available()
            if kept and available > max(10, len(kept) * 2):
                logger.warning(
                    "STOCK sheet mencurigakan (hanya %d baris vs %d stok lokal) — lewati penghapusan",
                    len(kept),
                    available,
                )
            else:
                db.delete_stock_not_in(kept)
            for row in fetch_csv(config.SETTINGS_URL):
                if row.get("KEY"):
                    db.set_setting(str(row["KEY"]).strip(), row.get("VALUE", ""))
            logger.info("Sinkronisasi dari spreadsheet selesai")
            return True
        except Exception as e:
            logger.error("Gagal sinkronisasi: %s", e)
            return False
