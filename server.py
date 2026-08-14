"""
Library Management System — Flask + CSV Backend
================================================
Run:  python server.py
Then open:  http://localhost:5000

Data is stored in the  data/  folder as CSV files.
One CSV per entity; the entire state is assembled/disassembled on every
GET /api/data  and  POST /api/data  call so the existing JS front-end
requires zero modifications.
"""

import csv
import json
import os
import random
import shutil
import string
import time
import io
import zipfile
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, send_file

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
INDEX_HTML = os.path.join(BASE_DIR, "index.html")
PORT       = 5000

# CSV file paths
F_BUSINESS   = os.path.join(DATA_DIR, "business.csv")
F_USERS      = os.path.join(DATA_DIR, "users.csv")
F_SETTINGS   = os.path.join(DATA_DIR, "settings.csv")
F_MEMBERS    = os.path.join(DATA_DIR, "members.csv")
F_SEATS      = os.path.join(DATA_DIR, "seats.csv")
F_PLANS      = os.path.join(DATA_DIR, "plans.csv")
F_FEE_TX     = os.path.join(DATA_DIR, "fee_transactions.csv")
F_EXPENSES   = os.path.join(DATA_DIR, "expenses.csv")
F_ATTENDANCE = os.path.join(DATA_DIR, "attendance.csv")
F_AUDIT      = os.path.join(DATA_DIR, "audit_log.csv")

# Column definitions (order matters for readability)
COLS = {
    "business":   ["name", "tagline", "address", "phone"],
    "users":      ["id", "username", "password", "name", "role"],
    "settings":   ["key", "value"],
    "members":    ["id", "memberId", "name", "mobile", "emergencyContact",
                   "address", "course", "idProof", "joiningDate", "seatId",
                   "planId", "validityEnd", "dueAmount", "status", "photo",
                   "createdAt"],
    "seats":      ["id", "number", "type", "status", "memberId", "reservedNote"],
    "plans":      ["id", "name", "duration", "months", "price",
                   "admissionFee", "seatType"],
    "fee_tx":     ["id", "memberId", "type", "amount", "mode", "date",
                   "notes", "receiptNo"],
    "expenses":   ["id", "category", "description", "amount", "mode", "date"],
    "attendance": ["memberId", "date"],
    "audit":      ["id", "ts", "user", "action", "details"],
}

app = Flask(__name__)

# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _read(filepath, fieldnames):
    """Read a CSV and return list of dicts.  Missing file → empty list."""
    if not os.path.exists(filepath):
        return []
    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def _write(filepath, fieldnames, rows):
    """Write list-of-dicts to CSV, creating the file (and dir) as needed."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Ensure every expected field exists (fill blanks)
            clean = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(clean)


def _backup():
    """Copy all CSV files to data/backup/ with a timestamp suffix."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(DATA_DIR, "backup", ts)
    os.makedirs(bdir, exist_ok=True)
    for fname in os.listdir(DATA_DIR):
        if fname.endswith(".csv"):
            shutil.copy2(os.path.join(DATA_DIR, fname),
                         os.path.join(bdir, fname))


# ---------------------------------------------------------------------------
# State → CSV
# ---------------------------------------------------------------------------

def _cast_number(v, default=0):
    try:
        return float(v) if "." in str(v) else int(v)
    except (TypeError, ValueError):
        return default


def save_state(state: dict):
    """Persist every sub-collection from the JS state object to CSV files."""

    # -- business (single-row) --
    b = state.get("business", {})
    _write(F_BUSINESS, COLS["business"], [b])

    # -- users --
    _write(F_USERS, COLS["users"], state.get("auth", {}).get("users", []))

    # -- settings (key-value pairs) --
    s = state.get("settings", {})
    rows = []
    rows.append({"key": "receiptCounter",  "value": s.get("receiptCounter", 1000)})
    rows.append({"key": "memberCounter",   "value": s.get("memberCounter", 1)})
    # seatTypes stored as JSON-encoded list in a single cell
    rows.append({"key": "seatTypes",       "value": json.dumps(s.get("seatTypes", []))})
    _write(F_SETTINGS, COLS["settings"], rows)

    # -- members --
    _write(F_MEMBERS, COLS["members"], state.get("members", []))

    # -- seats --
    _write(F_SEATS, COLS["seats"], state.get("seats", []))

    # -- plans --
    _write(F_PLANS, COLS["plans"], state.get("plans", []))

    # -- fee transactions --
    _write(F_FEE_TX, COLS["fee_tx"], state.get("feeTransactions", []))

    # -- expenses --
    _write(F_EXPENSES, COLS["expenses"], state.get("expenses", []))

    # -- attendance --
    _write(F_ATTENDANCE, COLS["attendance"], state.get("attendance", []))

    # -- audit log (cap at 500) --
    _write(F_AUDIT, COLS["audit"], state.get("auditLog", [])[:500])


# ---------------------------------------------------------------------------
# CSV → State
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Read all CSV files and assemble the JS-compatible state object."""

    # -- business --
    biz_rows = _read(F_BUSINESS, COLS["business"])
    business = biz_rows[0] if biz_rows else {
        "name": "Utkranti Library Ganj Basoda",
        "tagline": "Library Manager",
        "address": "",
        "phone": "",
    }

    # -- users --
    users = _read(F_USERS, COLS["users"])
    if not users:
        users = [{
            "id": "u1",
            "username": "admin",
            "password": "admin123",
            "name": "Administrator",
            "role": "Admin",
        }]

    # -- settings --
    srows = _read(F_SETTINGS, COLS["settings"])
    smap = {r["key"]: r["value"] for r in srows}
    seat_types = json.loads(smap.get("seatTypes", '["General","Cabin","AC Premium"]'))
    settings = {
        "seatTypes":      seat_types,
        "receiptCounter": _cast_number(smap.get("receiptCounter", 1000)),
        "memberCounter":  _cast_number(smap.get("memberCounter", 1)),
    }

    # -- members --
    raw_members = _read(F_MEMBERS, COLS["members"])
    members = []
    for m in raw_members:
        m["dueAmount"]  = _cast_number(m.get("dueAmount", 0))
        members.append(m)

    # -- seats --
    raw_seats = _read(F_SEATS, COLS["seats"])
    if not raw_seats:
        # Default 20 seats on first run
        raw_seats = []
        for i in range(1, 21):
            raw_seats.append({
                "id":           f"st{i}",
                "number":       "S" + str(i).zfill(2),
                "type":         "General" if i <= 14 else ("Cabin" if i <= 18 else "AC Premium"),
                "status":       "vacant",
                "memberId":     "",
                "reservedNote": "",
            })
        _write(F_SEATS, COLS["seats"], raw_seats)
    seats = raw_seats

    # -- plans --
    raw_plans = _read(F_PLANS, COLS["plans"])
    if not raw_plans:
        raw_plans = [
            {"id":"p1","name":"Monthly - General",   "duration":"monthly",    "months":1,  "price":800,  "admissionFee":100,"seatType":"General"},
            {"id":"p2","name":"Quarterly - General",  "duration":"quarterly",  "months":3,  "price":2200, "admissionFee":100,"seatType":"General"},
            {"id":"p3","name":"Half-Yearly - General","duration":"half-yearly","months":6,  "price":4200, "admissionFee":100,"seatType":"General"},
            {"id":"p4","name":"Yearly - General",     "duration":"yearly",     "months":12, "price":8000, "admissionFee":100,"seatType":"General"},
            {"id":"p5","name":"Monthly - Cabin",      "duration":"monthly",    "months":1,  "price":1200, "admissionFee":150,"seatType":"Cabin"},
            {"id":"p6","name":"Monthly - AC Premium", "duration":"monthly",    "months":1,  "price":1600, "admissionFee":200,"seatType":"AC Premium"},
        ]
        _write(F_PLANS, COLS["plans"], raw_plans)
    plans = []
    for p in raw_plans:
        p["months"]      = _cast_number(p.get("months", 1))
        p["price"]       = _cast_number(p.get("price", 0))
        p["admissionFee"]= _cast_number(p.get("admissionFee", 0))
        plans.append(p)

    # -- fee transactions --
    raw_tx = _read(F_FEE_TX, COLS["fee_tx"])
    fee_tx = []
    for t in raw_tx:
        t["amount"]    = _cast_number(t.get("amount", 0))
        t["receiptNo"] = _cast_number(t.get("receiptNo", 0))
        fee_tx.append(t)

    # -- expenses --
    raw_exp = _read(F_EXPENSES, COLS["expenses"])
    expenses = []
    for e in raw_exp:
        e["amount"] = _cast_number(e.get("amount", 0))
        expenses.append(e)

    # -- attendance --
    attendance = _read(F_ATTENDANCE, COLS["attendance"])

    # -- audit log --
    audit = _read(F_AUDIT, COLS["audit"])

    return {
        "business":        business,
        "auth":            {"users": users},
        "settings":        settings,
        "members":         members,
        "seats":           seats,
        "plans":           plans,
        "feeTransactions": fee_tx,
        "expenses":        expenses,
        "attendance":      attendance,
        "auditLog":        audit,
    }


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/data", methods=["GET"])
def api_get():
    try:
        state = load_state()
        return jsonify(state)
    except Exception as exc:
        app.logger.error("load_state failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/data", methods=["POST"])
def api_post():
    try:
        state = request.get_json(force=True)
        if not state:
            return jsonify({"error": "no body"}), 400
        save_state(state)
        return jsonify({"ok": True})
    except Exception as exc:
        app.logger.error("save_state failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Wipe all CSV data and re-seed factory defaults (admin/admin123)."""
    try:
        # Delete every CSV in data/
        for fname in os.listdir(DATA_DIR):
            fpath = os.path.join(DATA_DIR, fname)
            if fname.endswith(".csv") and os.path.isfile(fpath):
                os.remove(fpath)
        # Re-seed defaults by calling load_state (triggers default fallback)
        # then save_state to write fresh CSVs
        fresh = load_state()
        save_state(fresh)
        app.logger.info("Factory reset performed.")
        return jsonify({"ok": True})
    except Exception as exc:
        app.logger.error("reset failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

@app.route("/api/backup/export", methods=["GET"])
def api_backup_export():
    """Create an in-memory ZIP of all CSV files in data/ and send it."""
    try:
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname in os.listdir(DATA_DIR):
                if fname.endswith(".csv"):
                    fpath = os.path.join(DATA_DIR, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, arcname=fname)
        memory_file.seek(0)
        
        filename = f"lms_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        return send_file(
            memory_file,
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename
        )
    except Exception as exc:
        app.logger.error("backup export failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

# Serve any static files that might be added later (images, etc.)
@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


# ---------------------------------------------------------------------------
# Bootstrap & run
# ---------------------------------------------------------------------------

def bootstrap():
    """On first run, create the data directory and seed default CSV files."""
    os.makedirs(DATA_DIR, exist_ok=True)
    # Only seed if no data exists yet
    if not os.path.exists(F_BUSINESS):
        state = load_state()   # triggers default-value fallback
        save_state(state)
        print("[LMS] First run — default data files created in ./data/")


if __name__ == "__main__":
    bootstrap()
    print("=" * 60)
    print("  Library Management System -- Flask + CSV Backend")
    print("=" * 60)
    print(f"  Server running at:  http://localhost:{PORT}")
    print("  Data folder:        ./data/  (CSV files)")
    print("  Default login:      admin / admin123")
    print("  Press  Ctrl+C  to stop")
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=False)
