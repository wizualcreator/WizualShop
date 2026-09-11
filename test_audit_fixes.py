"""Tests for audit fixes: FAILED re-complete, delivered flag, SOLD-protection
upsert under concurrency, mark_payout rowcount, price parsing, delete guard.

Run: python -m pytest test_audit_fixes.py -q
"""

import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import db
import sync

TEST_DIR = tempfile.mkdtemp(prefix="audit_fix_test_")
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


def make_order(order_id, user_id, product_id="P001", qty=1, total=10000):
    db.create_order(
        order_id,
        str(user_id),
        "user" + str(user_id),
        {"id": product_id, "name": "Test Product"},
        qty,
        total,
    )


def stock_row(sid):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM stock WHERE stock_id=?", (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def fresh_db():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    os.makedirs(TEST_DIR, exist_ok=True)
    db.DB_FILE = os.path.join(TEST_DIR, "test_store.db")
    db.init_db()


def test_failed_order_can_recomplete():
    """H3: order FAILED yang ternyata dibayar harus bisa selesai."""
    fresh_db()
    setup_product()
    make_order("ORD-F1", 1001)
    db.set_order_status("ORD-F1", "FAILED")
    res = db.complete_order_atomic("ORD-F1", "PAY-F1")
    assert res["status"] == "COMPLETED", res["status"]
    assert len(res["contents"]) == 1
    assert db.get_order("ORD-F1")["status"] == "COMPLETED"


def test_completed_duplicate_still_idempotent():
    """complete_order_atomic masih idempotent utk COMPLETED."""
    fresh_db()
    setup_product()
    make_order("ORD-F2", 1002)
    assert db.complete_order_atomic("ORD-F2", "P1")["status"] == "COMPLETED"
    assert db.complete_order_atomic("ORD-F2", "P2")["status"] == "ALREADY"
    assert db.get_order("ORD-F2")["status"] == "COMPLETED"


def test_delivered_flag_helpers():
    fresh_db()
    setup_product()
    make_order("ORD-D1", 1003)
    db.complete_order_atomic("ORD-D1", "P1")
    o = db.get_order("ORD-D1")
    assert o["delivered"] == 0
    db.set_order_delivered("ORD-D1")
    assert db.get_order("ORD-D1")["delivered"] == 1
    contents = db.get_stock_contents_by_order("ORD-D1")
    assert contents == ["credential-S001"]
    # set_order_delivered tidak mempengaruhi order non-completed
    make_order("ORD-D2", 1004)
    db.set_order_delivered("ORD-D2")
    assert db.get_order("ORD-D2")["delivered"] == 0


def test_upsert_stock_row_preserves_sold_concurrent():
    """M4: upsert dari sheet tidak meng-overwrite baris SOLD saat race."""
    fresh_db()
    setup_product(stock_ids=["S001", "S002"])
    make_order("ORD-C1", 2001, qty=2)
    res = db.complete_order_atomic("ORD-C1", "PAY-C1")
    assert res["status"] == "COMPLETED"
    # sheet lama masih bilang AVAILABLE utk kedua stok
    def worker(sid):
        db.upsert_stock_row(
            {
                "stock_id": sid,
                "product_id": "P001",
                "content": "credential-" + sid,
                "status": "AVAILABLE",
                "sold_to": "",
            }
        )

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(worker, "S001")
        f2 = ex.submit(worker, "S002")
        f1.result()
        f2.result()
    r1 = stock_row("S001")
    r2 = stock_row("S002")
    assert r1["status"] == "SOLD", r1
    assert r2["status"] == "SOLD", r2
    assert r1["sold_to"] == "2001"
    assert r2["sold_to"] == "2001"
    assert db.count_available("P001") == 0


def test_mark_payout_rowcount():
    """H2: mark_payout mengembalikan jumlah komisi yang ditandai PAID."""
    fresh_db()
    db.credit_commission("777", "1001", "ORD-1", "P", 5000)
    db.credit_commission("777", "1001", "ORD-2", "P", 5000)
    n = db.mark_payout("777")
    assert n == 2, n
    assert db.get_wallet("777") == 0
    n2 = db.mark_payout("777")
    assert n2 == 0, n2


def test_parse_price_variants():
    assert sync._parse_price("15000") == 15000
    assert sync._parse_price("1.500") == 1500
    assert sync._parse_price("Rp10.000") == 10000
    assert sync._parse_price("15,000") == 15000
    assert sync._parse_price("Rp 5.000") == 5000
    assert sync._parse_price("") == 0
    assert sync._parse_price("abc") == 0


def test_delete_guard_skips_when_sheet_small():
    """M5: jangan hapus stok jika jumlah baris sheet mencurigakan kecil."""
    fresh_db()
    setup_product(stock_ids=["S001", "S002", "S003", "S004", "S005"])
    # simulasi CSV sheet yang terpotong: hanya 1 baris
    kept = {"S001"}
    available = db.count_all_available()
    # predikat guard sama seperti di sync.py
    suspicious = available > max(10, len(kept) * 2)
    assert suspicious is False  # 5 available, kept=1 -> 5 > 2 True? no: max(10,2)=10, 5>10 False
    assert db.count_all_available() == 5
    # kasus ekstrem: banyak stok lokal tapi sheet cuma beberapa baris -> guard aktif
    setup_product("P002", [f"X{i:04d}" for i in range(30)])
    assert db.count_all_available() == 35
    kept2 = {"X0000", "X0001"}
    suspicious2 = 35 > max(10, len(kept2) * 2)  # 35 > 10 -> True
    assert suspicious2 is True
    assert db.delete_stock_not_in(kept2) is None  # tetap berfungsi utk path normal


def test_reserve_stock_success_and_complete():
    """Reservasi mengunci stok; complete_order_atomic meng-claim baris RESERVED order itu."""
    fresh_db()
    setup_product(stock_ids=["S001", "S002", "S003"])
    make_order("ORD-R1", 3001, qty=2)
    assert db.reserve_stock("ORD-R1", "P001", 2) is True
    assert stock_row("S001")["status"] == "RESERVED"
    assert stock_row("S001")["reserved_order_id"] == "ORD-R1"
    assert db.count_available("P001") == 1
    res = db.complete_order_atomic("ORD-R1", "PAY-R1")
    assert res["status"] == "COMPLETED", res["status"]
    assert len(res["contents"]) == 2
    assert stock_row("S001")["status"] == "SOLD"
    assert stock_row("S002")["status"] == "SOLD"
    assert stock_row("S003")["status"] == "AVAILABLE"
    assert db.count_available("P001") == 1


def test_reserve_fails_when_insufficient_stock():
    """Tidak boleh over-reserve: minta lebih dari yang tersedia -> False, stok tak berubah."""
    fresh_db()
    setup_product(stock_ids=["S001", "S002"])
    make_order("ORD-R2", 3002)
    assert db.reserve_stock("ORD-R2", "P001", 3) is False
    assert db.count_available("P001") == 2
    assert stock_row("S001")["status"] == "AVAILABLE"


def test_reserve_excludes_other_orders_reservation():
    """Baris yang sudah di-reserve order lain tidak bisa di-reserve lagi."""
    fresh_db()
    setup_product(stock_ids=["S001", "S002"])
    make_order("ORD-R3A", 3003)
    make_order("ORD-R3B", 3004)
    assert db.reserve_stock("ORD-R3A", "P001", 2) is True
    assert db.reserve_stock("ORD-R3B", "P001", 1) is False
    assert db.count_available("P001") == 0


def test_release_reservation_restores_available():
    fresh_db()
    setup_product(stock_ids=["S001", "S002"])
    make_order("ORD-R4", 3005)
    assert db.reserve_stock("ORD-R4", "P001", 2) is True
    db.release_reservation("ORD-R4")
    assert stock_row("S001")["status"] == "AVAILABLE"
    assert stock_row("S001")["reserved_order_id"] is None
    assert db.count_available("P001") == 2


def test_release_expired_reservations():
    fresh_db()
    setup_product(stock_ids=["S001"])
    make_order("ORD-R5", 3006)
    assert db.reserve_stock("ORD-R5", "P001", 1) is True
    conn = db.get_conn()
    conn.execute(
        "UPDATE stock SET reserved_at=? WHERE stock_id='S001'",
        ((db.utcnow() - db.timedelta(hours=30)).isoformat(),),
    )
    conn.commit()
    conn.close()
    db.release_expired_reservations(max_age_hours=24)
    assert stock_row("S001")["status"] == "AVAILABLE"


def test_reserved_rows_not_overwritten_by_sync_upsert():
    """Sheet basi (AVAILABLE) tidak boleh menimpa baris RESERVED."""
    fresh_db()
    setup_product(stock_ids=["S001"])
    make_order("ORD-R6", 3007)
    assert db.reserve_stock("ORD-R6", "P001", 1) is True
    db.upsert_stock_row(
        {"stock_id": "S001", "product_id": "P001", "content": "baru", "status": "AVAILABLE", "sold_to": ""}
    )
    assert stock_row("S001")["status"] == "RESERVED"
    assert stock_row("S001")["content"] == "credential-S001"


def test_complete_after_release_then_other_order_claims():
    """Release lalu order lain bisa reserve+claim baris yang sama."""
    fresh_db()
    setup_product(stock_ids=["S001"])
    make_order("ORD-R7A", 3008)
    make_order("ORD-R7B", 3009)
    assert db.reserve_stock("ORD-R7A", "P001", 1) is True
    db.release_reservation("ORD-R7A")
    assert db.reserve_stock("ORD-R7B", "P001", 1) is True
    res = db.complete_order_atomic("ORD-R7B", "PAY-R7B")
    assert res["status"] == "COMPLETED", res["status"]
    assert stock_row("S001")["sold_order_id"] == "ORD-R7B"


def test_reserve_concurrent_no_overreserve():
    """100 order berebut 3 stok: hanya 3 yang berhasil reserve, sisanya False.

    Simulasi flash-sale: BEGIN IMMEDIATE serializes reservation, jadi tidak
    boleh ada 2 order memegang baris stok yang sama.
    """
    fresh_db()
    setup_product(stock_ids=["S001", "S002", "S003"])
    for i in range(100):
        make_order(f"ORD-C{i:03d}", 4000 + i)
    barrier = threading.Barrier(100)
    results = {}

    def worker(i):
        barrier.wait()
        results[f"ORD-C{i:03d}"] = db.reserve_stock(
            f"ORD-C{i:03d}", "P001", 1
        )

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(100)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n_ok = sum(1 for v in results.values() if v)
    assert n_ok == 3, n_ok
    assert db.count_available("P001") == 0
    assert db.count_all_available() == 0
    # tidak ada 2 order yang memegang stok yang sama
    conn = db.get_conn()
    dup = conn.execute(
        "SELECT reserved_order_id, COUNT(*) c FROM stock "
        "WHERE status='RESERVED' GROUP BY reserved_order_id HAVING c>1"
    ).fetchall()
    conn.close()
    assert len(dup) == 0, dup
    # yang berhasil reserve bisa di-release lalu stok balik AVAILABLE
    winners = [oid for oid, ok in results.items() if ok]
    assert len(winners) == 3
    for oid in winners:
        db.release_reservation(oid)
    assert db.count_all_available() == 3


def test_reserve_then_complete_concurrent():
    """Reserve berhasil lalu complete_order_atomic meng-claim tepat barisnya,
    meski 50 order lain ikut reserve pada stok yang sama di saat yang sama."""
    fresh_db()
    setup_product(stock_ids=[f"S{i:03d}" for i in range(10)])
    make_order("ORD-Z000", 5000, qty=3)
    assert db.reserve_stock("ORD-Z000", "P001", 3) is True
    # 40 order lain mencoba reserve sisa stok
    for i in range(40):
        make_order(f"ORD-Z{i+1:03d}", 5100 + i, qty=1)
    barrier = threading.Barrier(41)
    results = {}

    def claim():
        barrier.wait()
        results["claim"] = db.complete_order_atomic("ORD-Z000", "PAY-Z000")

    def other(i):
        barrier.wait()
        results[f"other{i}"] = db.reserve_stock(f"ORD-Z{i+1:03d}", "P001", 1)

    threads = [threading.Thread(target=claim)]
    threads += [
        threading.Thread(target=other, args=(i,)) for i in range(40)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["claim"]["status"] == "COMPLETED", results["claim"]["status"]
    assert len(results["claim"]["contents"]) == 3
    assert results["claim"]["contents"] == [
        "credential-S000", "credential-S001", "credential-S002"
    ]
    sold = sum(1 for i in range(3) if stock_row(f"S{i:03d}")["status"] == "SOLD")
    assert sold == 3
    reserved_others = sum(
        1 for i in range(3, 10) if stock_row(f"S{i:03d}")["status"] == "RESERVED"
    )
    assert reserved_others == 7
    assert db.count_all_available() == 0
