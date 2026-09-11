"""Integration tests for atomic inventory claim / first-successful-payment-wins.

Run: python test_inventory.py
Uses a temporary DB file, no Telegram network calls needed.
"""

import os
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import db

TEST_DIR = tempfile.mkdtemp(prefix="inventory_test_")
db.DB_FILE = os.path.join(TEST_DIR, "test_store.db")


def setup_product(product_id="P001", stock_ids=None, price=10000):
    stock_ids = stock_ids or ["S001"]
    db.upsert_product(
        {
            "id": product_id,
            "name": "Test Product",
            "emoji": "📦",
            "price": price,
            "status": "ACTIVE",
            "description": "desc",
        }
    )
    for sid in stock_ids:
        db.upsert_stock_row(
            {
                "stock_id": sid,
                "product_id": product_id,
                "content": f"credential-{sid}",
                "status": "AVAILABLE",
                "sold_to": "",
            }
        )


def make_order(order_id, user_id, product_id, qty=1, total=10000):
    db.create_order(
        order_id,
        str(user_id),
        "user" + str(user_id),
        {"id": product_id, "name": "Test Product"},
        qty,
        total,
    )


def available(product_id):
    return db.count_available(product_id)


def stock_row(sid):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM stock WHERE stock_id=?", (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def order(order_id):
    return db.get_order(order_id)


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def fresh_db():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    os.makedirs(TEST_DIR, exist_ok=True)
    db.DB_FILE = os.path.join(TEST_DIR, "test_store.db")
    db.init_db()


# ---------------- TEST 1: checkout -> pending, product stays AVAILABLE ----------------
def test1():
    print("TEST 1 — checkout: pending tidak mengunci stok")
    fresh_db()
    setup_product()
    make_order("ORD-A1", 1001, "P001")
    o = order("ORD-A1")
    check("order A status PENDING", o and o["status"] == "PENDING")
    check("product masih AVAILABLE", available("P001") == 1, f"got {available('P001')}")
    r = stock_row("S001")
    check(
        "sold_to kosong & status AVAILABLE",
        r["status"] == "AVAILABLE" and not r["sold_to"],
    )


# ---------------- TEST 2: A dan B checkout produk sama, keduanya pending ----------------
def test2():
    print("TEST 2 — dua checkout produk sama, keduanya pending")
    fresh_db()
    setup_product()
    make_order("ORD-A2", 1001, "P001")
    make_order("ORD-B2", 1002, "P001")
    oa = order("ORD-A2")
    ob = order("ORD-B2")
    check("A pending", oa["status"] == "PENDING")
    check("B pending", ob["status"] == "PENDING")
    check(
        "produk tetap AVAILABLE (1 stok)",
        available("P001") == 1,
        f"got {available('P001')}",
    )
    r = stock_row("S001")
    check("tidak ada sold_to", not r["sold_to"] and r["status"] == "AVAILABLE")


# ---------------- TEST 3: B bayar duluan -> B menang ----------------
def test3():
    print("TEST 3 — B membayar pertama, B menang")
    fresh_db()
    setup_product()
    make_order("ORD-A3", 1001, "P001")
    make_order("ORD-B3", 1002, "P001")

    res_b = db.complete_order_atomic("ORD-B3", "PAY-B3")
    check("B COMPLETED", res_b["status"] == "COMPLETED", res_b["status"])
    check("B menerima 1 produk", len(res_b["contents"]) == 1)
    check("B menerima credential benar", res_b["contents"][0] == "credential-S001")

    oa = order("ORD-A3")
    ob = order("ORD-B3")
    check("Order B COMPLETED", ob["status"] == "COMPLETED")
    check("Order A masih PENDING", oa["status"] == "PENDING", oa["status"])
    r = stock_row("S001")
    check("stok SOLD", r["status"] == "SOLD")
    check("sold_to = B", r["sold_to"] == "1002", r["sold_to"])
    check("sold_order_id = ORD-B3", r["sold_order_id"] == "ORD-B3", r["sold_order_id"])
    check("sold_at terisi", bool(r["sold_at"]))
    check("produk tidak diberikan ke A", "credential-S001" not in (oa and oa.get("status") or ""))


# ---------------- TEST 4: A bayar setelah B -> out-of-stock flow ----------------
def test4():
    print("TEST 4 — A membayar setelah B -> out of stock")
    fresh_db()
    setup_product()
    make_order("ORD-A4", 1001, "P001")
    make_order("ORD-B4", 1002, "P001")

    db.complete_order_atomic("ORD-B4", "PAY-B4")
    res_a = db.complete_order_atomic("ORD-A4", "PAY-A4")

    check("A -> PAID_BUT_OUT_OF_STOCK", res_a["status"] == "PAID_BUT_OUT_OF_STOCK", res_a["status"])
    check("A tidak menerima produk", len(res_a["contents"]) == 0)
    r = stock_row("S001")
    check("ownership tetap B", r["sold_to"] == "1002", r["sold_to"])
    check("sold_order_id tetap ORD-B4", r["sold_order_id"] == "ORD-B4", r["sold_order_id"])
    check("sold_at tidak overwrite", r["sold_at"] and r["sold_at"] == r["sold_at"])
    oa = order("ORD-A4")
    check("Order A PAID_BUT_OUT_OF_STOCK", oa["status"] == "PAID_BUT_OUT_OF_STOCK", oa["status"])


# ---------------- TEST 5: concurrent payment ----------------
def test5():
    print("TEST 5 — pembayaran A & B bersamaan")
    fresh_db()
    setup_product()
    make_order("ORD-A5", 1001, "P001")
    make_order("ORD-B5", 1002, "P001")

    results = {}

    def worker(oid, pid):
        results[oid] = db.complete_order_atomic(oid, pid)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(worker, "ORD-A5", "PAY-A5")
        f2 = ex.submit(worker, "ORD-B5", "PAY-B5")
        f1.result()
        f2.result()

    statuses = [results[o]["status"] for o in ("ORD-A5", "ORD-B5")]
    n_completed = statuses.count("COMPLETED")
    n_oos = statuses.count("PAID_BUT_OUT_OF_STOCK")
    check("hanya 1 order COMPLETED", n_completed == 1, str(statuses))
    check("1 order PAID_BUT_OUT_OF_STOCK", n_oos == 1, str(statuses))

    r = stock_row("S001")
    check("hanya 1 stok SOLD", r["status"] == "SOLD")
    winner = results["ORD-A5"] if results["ORD-A5"]["status"] == "COMPLETED" else results["ORD-B5"]
    loser = results["ORD-B5"] if winner is results["ORD-A5"] else results["ORD-A5"]
    check("winner menerima credential", len(winner["contents"]) == 1)
    check("loser tidak menerima apa-apa", len(loser["contents"]) == 0)
    check("sold_order_id = winner", r["sold_order_id"] in ("ORD-A5", "ORD-B5"))
    check(
        "sold_to = winner user",
        r["sold_to"] == winner["order"]["telegram_id"],
        f"{r['sold_to']} vs {winner['order']['telegram_id']}",
    )
    oa = order("ORD-A5")
    ob = order("ORD-B5")
    check("A & B status konsisten (satu completed, satu OOS)", {oa["status"], ob["status"]} == {"COMPLETED", "PAID_BUT_OUT_OF_STOCK"}, f"{oa['status']}/{ob['status']}")


# ---------------- TEST 6: duplicate webhook ----------------
def test6():
    print("TEST 6 — webhook duplikat (idempotent)")
    fresh_db()
    setup_product()
    make_order("ORD-A6", 1001, "P001")

    r1 = db.complete_order_atomic("ORD-A6", "PAY-A6")
    r2 = db.complete_order_atomic("ORD-A6", "PAY-A6")
    r3 = db.complete_order_atomic("ORD-A6", "PAY-A6-AGAIN")

    check("pertama COMPLETED", r1["status"] == "COMPLETED", r1["status"])
    check("duplikat -> ALREADY", r2["status"] == "ALREADY", r2["status"])
    check("duplikat ketiga -> ALREADY", r3["status"] == "ALREADY", r3["status"])
    check("hanya 1x claim (1 stok SOLD)", stock_row("S001")["status"] == "SOLD")
    check("hanya 1 order COMPLETED", order("ORD-A6")["status"] == "COMPLETED")

    conn = db.get_conn()
    n_orders = conn.execute("SELECT COUNT(*) c FROM orders WHERE order_id='ORD-A6'").fetchone()["c"]
    conn.close()
    check("tidak ada order duplikat", n_orders == 1, f"{n_orders}")


# ---------------- TEST 7: replacement stock (produk dengan 2 stok) ----------------
def test7():
    print("TEST 7 — replacement stock SKU sama")
    fresh_db()
    setup_product(stock_ids=["S001", "S002"])
    make_order("ORD-A7", 1001, "P001")
    make_order("ORD-B7", 1002, "P001")

    ra = db.complete_order_atomic("ORD-A7", "PAY-A7")
    check("A COMPLETED (klaim S001)", ra["status"] == "COMPLETED", ra["status"])
    check("A dapat credential S001", ra["contents"] == ["credential-S001"], str(ra["contents"]))

    rb = db.complete_order_atomic("ORD-B7", "PAY-B7")
    check("B COMPLETED (klaim S002 pengganti)", rb["status"] == "COMPLETED", rb["status"])
    check("B dapat credential S002", rb["contents"] == ["credential-S002"], str(rb["contents"]))

    check("kedua stok SOLD", stock_row("S001")["status"] == "SOLD" and stock_row("S002")["status"] == "SOLD")
    check("tidak ada yang SOLD ganda", stock_row("S001")["sold_order_id"] == "ORD-A7" and stock_row("S002")["sold_order_id"] == "ORD-B7")
    check("A tidak dapat credential B", ra["contents"] == ["credential-S001"])


# ---------------- TEST 8: banyak concurrent claim di banyak stok ----------------
def test8():
    print("TEST 8 — 5 user claim 3 stok secara concurrent")
    fresh_db()
    setup_product(stock_ids=["S001", "S002", "S003"])
    for i in range(5):
        make_order(f"ORD-C{i}", 2000 + i, "P001")

    results = {}
    barrier = threading.Barrier(5)

    def worker(oid):
        barrier.wait()
        results[oid] = db.complete_order_atomic(oid, f"PAY-{oid}")

    threads = [threading.Thread(target=worker, args=(f"ORD-C{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    completed = [o for o, r in results.items() if r["status"] == "COMPLETED"]
    oos = [o for o, r in results.items() if r["status"] == "PAID_BUT_OUT_OF_STOCK"]
    check("persis 3 COMPLETED", len(completed) == 3, f"{len(completed)}")
    check("persis 2 PAID_BUT_OUT_OF_STOCK", len(oos) == 2, f"{len(oos)}")

    conn = db.get_conn()
    sold = conn.execute("SELECT COUNT(*) c FROM stock WHERE status='SOLD'").fetchone()["c"]
    conn.close()
    check("persis 3 stok SOLD", sold == 3, f"{sold}")

    conn = db.get_conn()
    rows = conn.execute(
        "SELECT sold_order_id, COUNT(*) c FROM stock WHERE status='SOLD' GROUP BY sold_order_id"
    ).fetchall()
    conn.close()
    check("tidak ada stok dimiliki dua order", all(r["c"] == 1 for r in rows), str([dict(r) for r in rows]))


def main():
    print(f"\n=== Inventory integration tests ===\n")
    test1()
    test2()
    test3()
    test4()
    test5()
    test6()
    test7()
    test8()
    print(f"\n=== HASIL: {PASS} passed, {FAIL} failed ===")
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()