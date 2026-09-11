import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("TOKEN", "").strip()

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "").strip()
PRODUCTS_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=PRODUCTS"
STOCK_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=STOCK"
SETTINGS_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=SETTINGS"
ORDERS_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=ORDERS"

PAYMENT_METHOD = os.environ.get("PAYMENT_METHOD", "static_qris").strip().lower()

AFFILIATE_PERCENT = int(os.environ.get("AFFILIATE_PERCENT", "5") or 0)

NEVAPEDIA_API_KEY = os.environ.get("NEVAPEDIA_API_KEY", "").strip()

QRIS_IMAGE_URL = os.environ.get("QRIS_IMAGE_URL", "").strip()

_admin_raw = os.environ.get("ADMIN_CHAT_ID", "").strip()
try:
    ADMIN_CHAT_ID = int(_admin_raw) if _admin_raw else None
except ValueError:
    ADMIN_CHAT_ID = None

SHEET_WRITE_URL = os.environ.get("SHEET_WRITE_URL", "").strip()
SHEET_WRITE_SECRET = os.environ.get("SHEET_WRITE_SECRET", "").strip()

TEST_MODE = os.environ.get("TEST_MODE", "false").lower() in ("1", "true", "yes")

CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@DigitalinUpdate")

# Banner welcome /start. Isi URL gambar publik ATAU file_id foto Telegram
# (biarkan kosong jika tidak ingin banner).
BANNER_URL = os.environ.get(
    "BANNER_URL",
    "https://i.postimg.cc/c4CtCs95/Banner-Shop.jpg",
).strip()
