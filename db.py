import sqlite3
import os
from datetime import datetime, timedelta, timezone


def utcnow():
    """Naive UTC now (setara datetime.utcnow(), tanpa deprecation)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

DB_FILE = os.path.join(os.path.dirname(__file__), "store.db")


def get_conn():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute(
        """CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT,
            emoji TEXT,
            price INTEGER,
            status TEXT,
            description TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS stock (
            stock_id TEXT PRIMARY KEY,
            product_id TEXT,
            content TEXT,
            status TEXT,
            sold_to TEXT,
            sold_order_id TEXT,
            sold_at TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            telegram_id TEXT,
            username TEXT,
            product_id TEXT,
            product_name TEXT,
            qty INTEGER,
            total INTEGER,
            status TEXT,
            payment_id TEXT,
            created_at TEXT,
            paid_at TEXT,
            delivered INTEGER DEFAULT 0
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS referrals (
            referred_uid TEXT PRIMARY KEY,
            referrer_uid TEXT NOT NULL,
            created_at TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_uid TEXT,
            referred_uid TEXT,
            order_id TEXT,
            product_name TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS wallets (
            uid TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )"""
    )
    _migrate_stock_columns(conn)
    _migrate_orders_columns(conn)
    conn.commit()
    conn.close()


def _column_names(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_stock_columns(conn):
    cols = _column_names(conn, "stock")
    if "sold_order_id" not in cols:
        conn.execute("ALTER TABLE stock ADD COLUMN sold_order_id TEXT")
    if "sold_at" not in cols:
        conn.execute("ALTER TABLE stock ADD COLUMN sold_at TEXT")
    if "reserved_order_id" not in cols:
        conn.execute("ALTER TABLE stock ADD COLUMN reserved_order_id TEXT")
    if "reserved_at" not in cols:
        conn.execute("ALTER TABLE stock ADD COLUMN reserved_at TEXT")


def _migrate_orders_columns(conn):
    cols = _column_names(conn, "orders")
    if "delivered" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN delivered INTEGER DEFAULT 0")


def upsert_product(p):
    conn = get_conn()
    conn.execute(
        """INSERT INTO products (id, name, emoji, price, status, description)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, emoji=excluded.emoji, price=excluded.price,
             status=excluded.status, description=excluded.description""",
        (p["id"], p["name"], p["emoji"], p["price"], p["status"], p["description"]),
    )
    conn.commit()
    conn.close()


def upsert_stock_row(s):
    status = str(s.get("status", "")).strip().upper()
    sold_to = s.get("sold_to", "").strip() if status == "SOLD" else ""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT status, sold_to FROM stock WHERE stock_id=?", (s["stock_id"],)
        ).fetchone()
        if existing and existing["status"] in ("SOLD", "RESERVED"):
            status = existing["status"]
            sold_to = existing["sold_to"] or sold_to
        conn.execute(
            """INSERT INTO stock (stock_id, product_id, content, status, sold_to, sold_order_id, sold_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(stock_id) DO UPDATE SET
                 product_id=excluded.product_id, content=excluded.content,
                 status=excluded.status, sold_to=excluded.sold_to,
                 sold_order_id=excluded.sold_order_id, sold_at=excluded.sold_at
                 WHERE stock.status NOT IN ('SOLD','RESERVED')""",
            (s["stock_id"], s["product_id"], s["content"], status, sold_to, s.get("sold_order_id", ""), s.get("sold_at", "")),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_stock_not_in(kept_ids):
    if not kept_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" for _ in kept_ids)
    conn.execute(
        f"DELETE FROM stock WHERE stock_id NOT IN ({placeholders}) AND status NOT IN ('SOLD','RESERVED')",
        sorted(kept_ids),
    )
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_active_products():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM products WHERE status='ACTIVE' ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_available(product_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM stock WHERE product_id=? AND status='AVAILABLE'",
        (product_id,),
    ).fetchone()
    conn.close()
    return row["c"]


def create_order(order_id, telegram_id, username, product, qty, total):
    conn = get_conn()
    conn.execute(
        """INSERT INTO orders (order_id, telegram_id, username, product_id, product_name, qty, total, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            order_id,
            str(telegram_id),
            username,
            product["id"],
            product["name"],
            qty,
            total,
            "PENDING",
            utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_order(order_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_order_status(order_id, status, payment_id=None, paid_at=None):
    conn = get_conn()
    if payment_id and paid_at:
        conn.execute(
            "UPDATE orders SET status=?, payment_id=?, paid_at=? WHERE order_id=?",
            (status, payment_id, paid_at, order_id),
        )
    else:
        conn.execute(
            "UPDATE orders SET status=? WHERE order_id=?", (status, order_id)
        )
    conn.commit()
    conn.close()


def update_payment_id(order_id, payment_id):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET payment_id=? WHERE order_id=?", (payment_id, order_id)
    )
    conn.commit()
    conn.close()


def get_pending_orders():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orders WHERE status='PENDING' ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reserve_stock(order_id, product_id, qty):
    """Atomically hold `qty` AVAILABLE stock rows for an order.

    Runs inside BEGIN IMMEDIATE. Returns True if all qty rows were reserved,
    False if insufficient stock (nothing reserved on failure). Reserved rows
    get status='RESERVED' + reserved_order_id, so concurrent reservations are
    serialized and a stock row is held for exactly one order.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("BEGIN IMMEDIATE")
    try:
        now = utcnow().isoformat()
        rows = c.execute(
            "SELECT stock_id FROM stock WHERE product_id=? AND status='AVAILABLE' "
            "ORDER BY stock_id LIMIT ?",
            (product_id, qty),
        ).fetchall()
        if len(rows) < qty:
            conn.rollback()
            conn.close()
            return False
        for r in rows:
            cur = c.execute(
                "UPDATE stock SET status='RESERVED', reserved_order_id=?, reserved_at=? "
                "WHERE stock_id=? AND status='AVAILABLE'",
                (order_id, now, r["stock_id"]),
            )
            if cur.rowcount != 1:
                conn.rollback()
                conn.close()
                return False
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.rollback()
        conn.close()
        raise


def release_reservation(order_id):
    """Free RESERVED stock rows back to AVAILABLE for an order."""
    conn = get_conn()
    conn.execute(
        "UPDATE stock SET status='AVAILABLE', reserved_order_id=NULL, reserved_at=NULL "
        "WHERE status='RESERVED' AND reserved_order_id=?",
        (order_id,),
    )
    conn.commit()
    conn.close()


def release_expired_reservations(max_age_hours=24):
    """Release reservations older than max_age_hours (abandoned payment intents)."""
    cutoff = (utcnow() - timedelta(hours=max_age_hours)).isoformat()
    conn = get_conn()
    conn.execute(
        "UPDATE stock SET status='AVAILABLE', reserved_order_id=NULL, reserved_at=NULL "
        "WHERE status='RESERVED' AND reserved_at < ?",
        (cutoff,),
    )
    conn.commit()
    conn.close()


def complete_order_atomic(order_id, payment_id):
    """Atomically claim inventory for a paid order.

    Runs inside a single BEGIN IMMEDIATE transaction so concurrent payment
    callbacks are serialized by SQLite. Only one order can flip a stock row
    from AVAILABLE -> SOLD; every UPDATE carries a compare-and-set guard
    (WHERE status='AVAILABLE') and a rowcount check.

    Returns dict: {"status", "order", "contents", "stock_ids"}
      - status: COMPLETED | PAID_BUT_OUT_OF_STOCK | ALREADY | NOT_FOUND
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("BEGIN IMMEDIATE")
    try:
        order = c.execute(
            "SELECT * FROM orders WHERE order_id=?", (order_id,)
        ).fetchone()
        if not order:
            conn.rollback()
            conn.close()
            return {"status": "NOT_FOUND", "order": None, "contents": [], "stock_ids": []}
        if order["status"] in ("COMPLETED", "PAID_BUT_OUT_OF_STOCK"):
            conn.rollback()
            conn.close()
            return {"status": "ALREADY", "order": dict(order), "contents": [], "stock_ids": []}

        now = utcnow().isoformat()
        rows = c.execute(
            """SELECT * FROM stock WHERE product_id=?
               AND (status='AVAILABLE'
                    OR (status='RESERVED' AND reserved_order_id=?))
               ORDER BY CASE WHEN status='RESERVED' THEN 0 ELSE 1 END, stock_id
               LIMIT ?""",
            (order["product_id"], order_id, order["qty"]),
        ).fetchall()
        if len(rows) < order["qty"]:
            conn.execute(
                "UPDATE stock SET status='AVAILABLE', reserved_order_id=NULL, reserved_at=NULL "
                "WHERE status='RESERVED' AND reserved_order_id=?",
                (order_id,),
            )
            c.execute(
                "UPDATE orders SET status='PAID_BUT_OUT_OF_STOCK', payment_id=?, paid_at=? WHERE order_id=?",
                (payment_id, now, order_id),
            )
            conn.commit()
            order = dict(order)
            order["status"] = "PAID_BUT_OUT_OF_STOCK"
            conn.close()
            return {
                "status": "PAID_BUT_OUT_OF_STOCK",
                "order": order,
                "contents": [],
                "stock_ids": [],
            }

        contents = []
        stock_ids = []
        for r in rows:
            cur = c.execute(
                "UPDATE stock SET status='SOLD', sold_to=?, sold_order_id=?, sold_at=?, "
                "reserved_order_id=NULL, reserved_at=NULL "
                "WHERE stock_id=? AND (status='AVAILABLE' "
                "OR (status='RESERVED' AND reserved_order_id=?))",
                (str(order["telegram_id"]), order_id, now, r["stock_id"], order_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                conn.close()
                return {
                    "status": "PAID_BUT_OUT_OF_STOCK",
                    "order": dict(order),
                    "contents": [],
                    "stock_ids": [],
                }
            contents.append(r["content"])
            stock_ids.append(r["stock_id"])

        c.execute(
            "UPDATE orders SET status='COMPLETED', payment_id=?, paid_at=? WHERE order_id=?",
            (payment_id, now, order_id),
        )
        conn.commit()
        order = dict(order)
        order["status"] = "COMPLETED"
        conn.close()
        return {
            "status": "COMPLETED",
            "order": order,
            "contents": contents,
            "stock_ids": stock_ids,
        }
    except Exception:
        conn.rollback()
        conn.close()
        raise


def get_my_orders(telegram_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orders WHERE telegram_id=? ORDER BY created_at DESC",
        (str(telegram_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_orders(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_order_delivered(order_id):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET delivered=1 WHERE order_id=? AND status='COMPLETED'",
        (order_id,),
    )
    conn.commit()
    conn.close()


def get_stock_contents_by_order(order_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT content FROM stock WHERE sold_order_id=? ORDER BY stock_id",
        (order_id,),
    ).fetchall()
    conn.close()
    return [r["content"] for r in rows]


def count_all_available():
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM stock WHERE status='AVAILABLE'"
    ).fetchone()
    conn.close()
    return row["c"]


def set_referred(referred_uid, referrer_uid):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO referrals (referred_uid, referrer_uid, created_at) VALUES (?,?,?)",
        (str(referred_uid), str(referrer_uid), utcnow().isoformat()),
    )
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok


def get_referrer(referred_uid):
    conn = get_conn()
    row = conn.execute(
        "SELECT referrer_uid FROM referrals WHERE referred_uid=?",
        (str(referred_uid),),
    ).fetchone()
    conn.close()
    return row["referrer_uid"] if row else None


def credit_commission(referrer_uid, referred_uid, order_id, product_name, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO commissions (referrer_uid, referred_uid, order_id, product_name, amount, status, created_at)
           VALUES (?,?,?,?,?,'PENDING',?)""",
        (
            str(referrer_uid),
            str(referred_uid),
            order_id,
            product_name,
            amount,
            utcnow().isoformat(),
        ),
    )
    c.execute(
        """INSERT INTO wallets (uid, balance) VALUES (?,?)
           ON CONFLICT(uid) DO UPDATE SET balance=balance+excluded.balance""",
        (str(referrer_uid), amount),
    )
    conn.commit()
    conn.close()


def get_wallet(uid):
    conn = get_conn()
    row = conn.execute(
        "SELECT balance FROM wallets WHERE uid=?", (str(uid),)
    ).fetchone()
    conn.close()
    return row["balance"] if row else 0


def count_referrals(uid):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM referrals WHERE referrer_uid=?", (str(uid),)
    ).fetchone()
    conn.close()
    return row["n"]


def get_commissions(uid, limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM commissions WHERE referrer_uid=? ORDER BY id DESC LIMIT ?",
        (str(uid), limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_wallets():
    conn = get_conn()
    rows = conn.execute(
        """SELECT w.uid, w.balance,
              (SELECT COUNT(*) FROM referrals r WHERE r.referrer_uid=w.uid) AS referrals,
              (SELECT COUNT(*) FROM commissions c WHERE c.referrer_uid=w.uid) AS total_comm
           FROM wallets w ORDER BY w.balance DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_payout(uid):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE commissions SET status='PAID' WHERE referrer_uid=? AND status='PENDING'",
        (str(uid),),
    )
    n = c.rowcount
    c.execute("UPDATE wallets SET balance=0 WHERE uid=?", (str(uid),))
    conn.commit()
    conn.close()
    return n
