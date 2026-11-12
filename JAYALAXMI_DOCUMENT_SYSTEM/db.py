"""SQLite access layer."""
import csv
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    ewb_no TEXT,
    extracted_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES uploads(id),
    ewb_no TEXT,
    receiver_name TEXT NOT NULL,
    invoice_value REAL,
    parcels INTEGER NOT NULL,
    original_pdf TEXT,
    data_json TEXT NOT NULL,
    outward_html TEXT NOT NULL,
    from_html TEXT NOT NULL,
    outward_file TEXT,
    from_file TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    address TEXT,
    mobile TEXT,
    booking TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        for k, v in config.DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))


def get_settings():
    with connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}


def save_settings(values):
    with connect() as c:
        for k, v in values.items():
            if k in config.DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))


def name_key(name):
    return " ".join((name or "").lower().split())


def find_customer(name):
    with connect() as c:
        return c.execute("SELECT * FROM customers WHERE name_key=?", (name_key(name),)).fetchone()


def export_customers_csv():
    """Mirror customers into Customer_Records/ so the data is visible outside the DB."""
    with connect() as c:
        rows = c.execute("SELECT name,address,mobile,booking FROM customers ORDER BY name").fetchall()
    with open(config.CUSTOMER_DIR / "customers.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Address", "Mobile", "Booking"])
        for r in rows:
            w.writerow([r["name"], r["address"], r["mobile"], r["booking"]])
