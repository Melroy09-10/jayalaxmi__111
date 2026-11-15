"""JAYALAXMI SILKS - Document Management System (local Flask app)."""
import hashlib
import io
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request, send_file,
                   session, url_for)
from werkzeug.utils import secure_filename

import config
import db
import extractor
import letters

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES


def _secret_key():
    """SECRET_KEY from the environment (hosting) or a random key kept next to the database."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    f = config.DB_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY") == "1",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 12)
db.init_db()
_failed = {"n": 0, "until": 0.0}


# --------------------------------------------------------------------------- optional login
# Local use needs no password. When the app is hosted online, set the APP_PASSWORD environment
# variable and every page (except the login page and static files) requires it.
def auth_required():
    return bool(os.environ.get("APP_PASSWORD"))


@app.before_request
def require_login():
    if not auth_required() or request.endpoint in ("login", "static"):
        return None
    if session.get("auth") is True:
        return None
    return redirect(url_for("login", next=request.full_path if request.method == "GET" else "/"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_required():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if time.time() < _failed["until"]:
            flash("Too many wrong attempts. Wait a minute and try again.", "danger")
        elif hmac.compare_digest(request.form.get("password", "").encode(), os.environ["APP_PASSWORD"].encode()):
            _failed["n"] = 0
            session.clear()
            session["auth"] = True
            session.permanent = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        else:
            _failed["n"] += 1
            if _failed["n"] >= 5:
                _failed.update(n=0, until=time.time() + 60)
            flash("Wrong password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


MOBILE_OK = re.compile(r"^[6-9]\d{9}$")


# --------------------------------------------------------------------------- helpers
@app.template_filter("inr")
def inr_filter(v):
    try:
        return f"Rs.{float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


@app.context_processor
def inject_globals():
    s = db.get_settings()
    return {"co": s, "today": datetime.now().strftime("%A, %d %B %Y"), "auth_on": auth_required()}


def clean_mobile(raw):
    """Return (one or more 10-digit mobiles joined as '9845100490 / 6362226302', error or None)."""
    parts = [p for p in re.split(r"[/,;]+", raw or "") if p.strip()]
    if len(parts) > 2:
        return "", "Enter at most 2 mobile numbers, each exactly 10 digits (separate two with /)."
    good = []
    for p in parts:
        digits = re.sub(r"[\s\-()]", "", p)
        digits = re.sub(r"^(\+?91|0)(?=\d{10}$)", "", digits)
        if not MOBILE_OK.match(digits):
            return ", ".join(good), "Each mobile number must be exactly 10 digits. You can enter 1 or 2 numbers (separate with /)."
        good.append(digits)
    return ", ".join(good), None


def unique_path(folder, filename):
    p = Path(folder) / filename
    n = 1
    while p.exists():
        p = Path(folder) / f"{Path(filename).stem}_{n}{Path(filename).suffix}"
        n += 1
    return p


def safe_child(folder, name):
    """Resolve ``name`` inside ``folder`` and refuse anything that escapes it."""
    base = Path(folder).resolve()
    p = (base / name).resolve()
    if base not in p.parents:
        abort(404)
    return p


def build_documents(data, when=None):
    settings = db.get_settings()
    ctx = letters.build_context(data, settings, when)
    return ctx, letters.render_outward(ctx), letters.render_from(ctx)


def load_doc(doc_id):
    with db.connect() as c:
        row = c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        abort(404)
    return row


# --------------------------------------------------------------------------- pages
@app.route("/")
def dashboard():
    return render_template("dashboard.html", active="dashboard", ocr=extractor.ocr_available())


def upload_error(msg):
    flash(msg, "danger")
    return redirect(url_for("dashboard"))


@app.route("/upload", methods=["POST"])
def upload():
    """Upload -> extract -> map into both letters -> preview. No data-entry step."""
    f = request.files.get("pdf")
    if not f or not f.filename:
        return upload_error("Please choose an E-Way Bill PDF to upload.")
    if not f.filename.lower().endswith(".pdf"):
        return upload_error("Only PDF files can be uploaded.")
    blob = f.read()
    if not blob.startswith(b"%PDF"):
        return upload_error("This file is not a valid PDF.")
    original = secure_filename(f.filename) or "ewaybill.pdf"
    stored = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{original}"
    try:
        dest = config.UPLOAD_DIR / stored
        dest.write_bytes(blob)
    except OSError as e:
        return upload_error(f"Could not save the file ({e.strerror or e}). Check folder permissions.")
    try:
        result = extractor.extract(dest)
    except extractor.ExtractionError as e:
        dest.unlink(missing_ok=True)
        return upload_error(str(e))
    except Exception as e:  # unexpected parser failure
        dest.unlink(missing_ok=True)
        return upload_error(f"Unexpected error while reading the PDF: {e}")
    ewb = result["fields"]["ewb_no"]
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO uploads(original_name,stored_name,sha256,ewb_no,extracted_json) VALUES(?,?,?,?,?)",
            (f.filename, stored, hashlib.sha256(blob).hexdigest(), ewb, json.dumps(result)))
        up = c.execute("SELECT * FROM uploads WHERE id=?", (cur.lastrowid,)).fetchone()
        dup = c.execute("SELECT id FROM documents WHERE ewb_no=? AND ewb_no<>'' LIMIT 1", (ewb,)).fetchone()
    return redirect(url_for("complete_upload", upload_id=up["id"]))


def finish_upload(up, result, answers):
    """Build the letters from the extracted data plus anything the user typed for missing details."""
    data = auto_data(result)
    for k, v in answers.items():
        data[k] = v
    data["warnings"] = result["warnings"] + data["warnings"]
    if "parcels" in answers:
        data["warnings"] = [w for w in data["warnings"] if "Parcel count" not in w]
    if answers.get("consignee_mobile"):
        data["warnings"] = [w for w in data["warnings"] if "mobile" not in w.lower()]
    with db.connect() as c:
        dup = c.execute("SELECT id FROM documents WHERE ewb_no=? AND ewb_no<>'' LIMIT 1", (up["ewb_no"],)).fetchone()
    if dup:
        data["warnings"].insert(0, f"Letters were already generated earlier for E-Way Bill {up['ewb_no']} "
                                   f"(document #{dup['id']}).")
    data.pop("missing", None)
    try:
        doc_id, warn = save_document(up, data)
    except (ValueError, sqlite3.Error, OSError) as e:
        return upload_error(f"Could not generate the letters: {e}")
    if warn:
        flash(warn, "warning")
    return redirect(url_for("view_document", doc_id=doc_id))


# details the letters cannot do without: key -> label shown when asking for it
REQUIRED_FIELDS = {
    "consignee_name": "Receiver name",
    "consignee_address": "Receiver address",
    "through": "Sending through (optional)",
    "invoice_value": "Total amount (Rs.)",
    "parcels": "Number of parcels",
    "consignee_mobile": "Receiver mobile number",
}


@app.route("/upload/<int:upload_id>/complete", methods=["GET", "POST"])
def complete_upload(upload_id):
    """Ask for details the E-Way Bill did not contain, instead of printing blanks."""
    with db.connect() as c:
        up = c.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
    if not up:
        abort(404)
    result = json.loads(up["extracted_json"])
    auto = auto_data(result)
    # fixed order on the form; name/address only appear when the E-Way Bill lacked them,
    # the rest are always shown (prefilled) so they can be confirmed or changed
    ORDER = ["consignee_name", "consignee_address", "consignee_mobile", "parcels", "invoice_value", "through"]
    always = {"consignee_mobile", "parcels", "invoice_value", "through"}
    missing = [k for k in ORDER if k in always or k in auto["missing"]]
    defaults = {"parcels": auto["parcels"], "invoice_value": "%.2f" % auto["invoice_value"] if auto["invoice_value"] != "" else "",
                "consignee_mobile": auto["consignee_mobile"], "through": auto["through"]}
    errors, answers = {}, {}
    if request.method == "POST":
        for k in missing:
            v = " ".join(request.form.get(k, "").split())
            if k == "consignee_mobile":
                answers[k], err = clean_mobile(v)
                if err:
                    errors[k] = err
            elif k == "parcels":
                if v.isdigit() and int(v) > 0:
                    answers[k] = int(v)
                else:
                    errors[k] = "Enter a whole number, e.g. 2"
            elif k == "through":
                answers[k] = v
            elif not v:
                errors[k] = "Please enter this detail."
            elif k == "invoice_value":
                try:
                    answers[k] = float(v.replace(",", ""))
                    if answers[k] <= 0:
                        raise ValueError
                except ValueError:
                    errors[k] = "Enter the amount as a number, e.g. 38851.00"
            else:
                answers[k] = v
        if not errors:
            return finish_upload(up, result, answers)
    return render_template("complete.html", up=up, missing=missing, labels=REQUIRED_FIELDS,
                           errors=errors, values=request.form if request.method == "POST" else defaults, active="dashboard")


def parse_bill_date(text):
    """'13/11/2026', '13-11-2026' or '13/11/2026 10:30 AM' -> datetime, or None."""
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def auto_data(result):
    """Map extracted fields straight into letter data. Required details that are
    genuinely missing are listed in ``missing`` so the user is asked for them - never invented."""
    fields = result["fields"]
    settings = db.get_settings()
    place = fields.get("delivery_location", "")
    warnings = []
    missing = []
    address = fields.get("consignee_address", "")
    from_place = False
    if not address and place:
        address, from_place = place, True
        warnings.append("The E-Way Bill has no full consignee address; the Place of Delivery was used.")
    elif not address:
        missing.append("consignee_address")
    mobile, err = clean_mobile(fields.get("consignee_mobile", ""))
    if err:
        mobile = ""
    if not mobile:
        warnings.append("No consignee mobile number was found; the Mob / PH line is left out.")
    booking = fields.get("booking_city", "").strip().upper()
    through = fields.get("transporter_name", "")
    if not fields.get("consignee_name"):
        missing.append("consignee_name")
    value = ""
    try:
        if fields.get("invoice_value"):
            value = float(fields["invoice_value"])
    except ValueError:
        pass
    if value == "":
        missing.append("invoice_value")
    pk = fields.get("packages", "")
    if pk.isdigit() and int(pk) > 0:
        parcels = int(pk)
    else:
        raw = settings.get("default_parcels", "1")
        parcels = int(raw) if raw.isdigit() and int(raw) > 0 else 1
        warnings.append(f"Parcel count is not in the E-Way Bill; the default of {parcels} was used "
                        f"(change the default in Settings).")
    bill_date = parse_bill_date(fields.get("ewb_date"))
    return {
        "letter_date": bill_date.strftime("%Y-%m-%d") if bill_date else "",
        "parcels": parcels, "consignee_name": fields.get("consignee_name", ""),
        "consignee_address": address, "consignee_mobile": mobile, "booking": booking,
        "through": through, "ewb_no": ewb_of(fields), "invoice_no": fields.get("invoice_no", ""),
        "invoice_value": value, "warnings": warnings, "missing": missing, "_address_from_place": from_place,
    }


def ewb_of(fields):
    return fields.get("ewb_no", "")


def save_document(up, data):
    """Render both letters, store them and write PDFs. Returns (doc_id, warning)."""
    ctx, outward_html, from_html = build_documents(data)
    snapshot = json.dumps({**data, "ctx": ctx})
    with db.connect() as c:
        cur = c.execute("""INSERT INTO documents(upload_id,ewb_no,receiver_name,invoice_value,parcels,
                           original_pdf,data_json,outward_html,from_html) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (up["id"], data["ewb_no"], data["consignee_name"] or "(name not found)",
                         data["invoice_value"] or None, int(data["parcels"]), up["original_name"],
                         snapshot, outward_html, from_html))
        new_id = cur.lastrowid
    return new_id, None


@app.route("/document/<int:doc_id>/mobile", methods=["POST"])
def set_mobile(doc_id):
    """Optional: add or change the receiver's mobile number. Regenerates both letters of this document."""
    doc = load_doc(doc_id)
    mobile, err = clean_mobile(request.form.get("mobile", ""))
    if err:
        flash(err, "danger")
        return redirect(url_for("view_document", doc_id=doc_id))
    data = {k: v for k, v in json.loads(doc["data_json"]).items() if k != "ctx"}
    data["consignee_mobile"] = mobile
    if mobile:
        data["warnings"] = [w for w in data.get("warnings", []) if "mobile" not in w.lower()]
    elif not any("mobile" in w.lower() for w in data.get("warnings", [])):
        data.setdefault("warnings", []).append("No consignee mobile number; the Mob / PH line is left out.")
    ctx, outward_html, from_html = build_documents(data, datetime.fromisoformat(doc["created_at"]))
    try:
        with db.connect() as c:
            c.execute("""UPDATE documents SET data_json=?,outward_html=?,from_html=?,
                         updated_at=datetime('now','localtime') WHERE id=?""",
                      (json.dumps({**data, "ctx": ctx}), outward_html, from_html, doc_id))
    except (ValueError, sqlite3.Error, OSError) as e:
        flash(f"Could not update the letters: {e}", "danger")
        return redirect(url_for("view_document", doc_id=doc_id))
    flash("Mobile number saved in both letters." if mobile else "Mobile number removed from both letters.", "success")
    return redirect(url_for("view_document", doc_id=doc_id))


@app.route("/document/<int:doc_id>")
def view_document(doc_id):
    """Read-only: opening a document never creates or modifies history."""
    doc = load_doc(doc_id)
    warnings = json.loads(doc["data_json"]).get("warnings", [])
    mobile = json.loads(doc["data_json"]).get("consignee_mobile", "")
    return render_template("preview.html", doc=doc, warnings=warnings, mobile=mobile, active="dashboard")


def fragments_for(doc, which):
    if which == "outward":
        return [doc["outward_html"]]
    if which == "from":
        return [doc["from_html"]]
    if which == "both":
        return [doc["outward_html"], doc["from_html"]]
    abort(404)


@app.route("/document/<int:doc_id>/print/<which>")
def print_document(doc_id, which):
    doc = load_doc(doc_id)
    return letters.wrap_print_page(fragments_for(doc, which), f"{doc['receiver_name']} - {which}")


@app.route("/document/<int:doc_id>/pdf/<which>")
def pdf_document(doc_id, which):
    doc = load_doc(doc_id)
    try:
        if which == "all":
            # print order: From letter, Outward letter, then the original E-Way Bill
            parts = [letters.fragment_to_pdf(doc["from_html"]), letters.fragment_to_pdf(doc["outward_html"])]
            with db.connect() as c:
                up = c.execute("SELECT stored_name FROM uploads WHERE id=?", (doc["upload_id"],)).fetchone()
            original = safe_child(config.UPLOAD_DIR, up["stored_name"]) if up else None
            if not original or not original.exists():
                flash("The original E-Way Bill file is no longer available, so all three cannot be printed.", "danger")
                return redirect(url_for("view_document", doc_id=doc_id))
            parts.append(original.read_bytes())
            blob = letters.merge_pdfs(parts)
        else:
            blob = letters.merge_pdfs([letters.fragment_to_pdf(f) for f in fragments_for(doc, which)])
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("view_document", doc_id=doc_id))
    tag = re.sub(r"\W+", "_", doc["receiver_name"]).strip("_") or "letter"
    # ?view=1 opens it in the browser (for printing) instead of downloading
    return send_file(io.BytesIO(blob), mimetype="application/pdf", as_attachment=not request.args.get("view"),
                     download_name=f"{tag}_{which}_{doc['id']}.pdf")


@app.route("/upload/<int:upload_id>/pdf")
def original_pdf(upload_id):
    with db.connect() as c:
        up = c.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
    if not up:
        abort(404)
    return send_file(safe_child(config.UPLOAD_DIR, up["stored_name"]), mimetype="application/pdf",
                     download_name=up["original_name"])


# --------------------------------------------------------------------------- letter format
SAMPLE_DATA = {
    "parcels": 2, "consignee_name": "SAMPLE RECEIVER NAME",
    "consignee_address": "No. 1, Sample Street, Sample Area,\nCity - 560001", "consignee_mobile": "9876543210",
    "booking": "CITY", "through": "Transporter Name", "ewb_no": "", "invoice_value": 38851.0,
}


def preview_pair(format_values):
    settings = {**db.get_settings(), **format_values}
    ctx = letters.build_context(SAMPLE_DATA, settings)
    return letters.render_outward(ctx), letters.render_from(ctx)


@app.route("/format", methods=["GET", "POST"])
def letter_format():
    """Edit the wording and look of both letters. Applies to letters generated from now on."""
    if request.method == "POST":
        if request.form.get("action") == "reset":
            db.save_settings(config.LETTER_DEFAULTS)
            flash("Letter format reset to the original.", "success")
        else:
            db.save_settings(letters.clean_letter(request.form))
            flash("Letter format saved. New letters will use it; existing documents are unchanged.", "success")
        return redirect(url_for("letter_format"))
    s = db.get_settings()
    vals = letters.clean_letter({k: s.get(k) for k in config.LETTER_DEFAULTS})
    return render_template("format.html", f=vals, fonts=config.FONT_CHOICES, active="format")


@app.route("/format/preview", methods=["POST"])
def format_preview():
    outward, from_ = preview_pair(letters.clean_letter(request.form))
    return {"outward": outward, "from": from_}


# --------------------------------------------------------------------------- settings / backup
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        vals = {k: request.form.get(k, "").strip() for k in config.DEFAULT_SETTINGS if k in request.form}
        vals["show_ewb_in_outward"] = "1" if request.form.get("show_ewb_in_outward") else "0"
        if "default_parcels" in vals and not (vals["default_parcels"].isdigit() and int(vals["default_parcels"]) > 0):
            vals["default_parcels"] = "1"
        if vals.get("date_format") not in config.DATE_FORMATS:
            vals.pop("date_format", None)
        vals.pop("output_folder", None)
        db.save_settings(vals)
        flash("Settings saved. They apply to new letters only; existing documents are unchanged.", "success")
        return redirect(url_for("settings"))
    backups = sorted(config.BACKUP_DIR.glob("*.zip"), reverse=True)[:8]
    return render_template("settings.html", s=db.get_settings(), formats=config.DATE_FORMATS,
                           backups=backups, active="settings")


def make_backup_zip(dest):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        # consistent DB copy even if something else holds the file open
        mem = sqlite3.connect(":memory:")
        with sqlite3.connect(config.DB_PATH) as src:
            src.backup(mem)
        tmp = config.DB_DIR / "_backup_tmp.db"
        disk = sqlite3.connect(tmp)
        mem.backup(disk)
        disk.close()
        mem.close()
        z.write(tmp, "database/jayalaxmi.db")
        tmp.unlink(missing_ok=True)
        for folder in ("Uploaded_PDFs", "Generated_Letters", "Customer_Records"):
            for f in (config.BASE_DIR / folder).rglob("*"):
                if f.is_file():
                    z.write(f, f"{folder}/{f.relative_to(config.BASE_DIR / folder).as_posix()}")


@app.route("/backup", methods=["POST"])
def backup():
    name = f"JAYALAXMI_backup_{datetime.now():%Y%m%d_%H%M%S}.zip"
    dest = config.BACKUP_DIR / name
    try:
        make_backup_zip(dest)
    except (OSError, sqlite3.Error) as e:
        flash(f"Backup failed: {e}", "danger")
        return redirect(url_for("settings"))
    return send_file(dest, as_attachment=True, download_name=name)


@app.route("/restore", methods=["POST"])
def restore():
    f = request.files.get("backup")
    if not request.form.get("confirm"):
        flash("Tick the confirmation box to restore. Restoring replaces all current data.", "danger")
        return redirect(url_for("settings"))
    if not f or not f.filename.lower().endswith(".zip"):
        flash("Choose a backup .zip file created by this application.", "danger")
        return redirect(url_for("settings"))
    blob = f.read()
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        flash("This file is not a valid ZIP archive.", "danger")
        return redirect(url_for("settings"))
    allowed = ("database/", "Uploaded_PDFs/", "Generated_Letters/", "Customer_Records/")
    names = z.namelist()
    if "database/jayalaxmi.db" not in names:
        flash("This ZIP is not a JAYALAXMI backup (database not found).", "danger")
        return redirect(url_for("settings"))
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts or not n.startswith(allowed):
            flash(f"Backup rejected: unexpected entry '{n}'.", "danger")
            return redirect(url_for("settings"))
    probe = config.DB_DIR / "_restore_probe.db"
    probe.write_bytes(z.read("database/jayalaxmi.db"))
    try:
        t = sqlite3.connect(probe)
        tables = {r[0] for r in t.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        t.close()
        if not {"documents", "customers", "settings"} <= tables:
            raise sqlite3.DatabaseError("missing tables")
    except sqlite3.Error:
        probe.unlink(missing_ok=True)
        flash("The database inside this backup is invalid.", "danger")
        return redirect(url_for("settings"))
    # safety copy of the current state first
    make_backup_zip(config.BACKUP_DIR / f"pre_restore_{datetime.now():%Y%m%d_%H%M%S}.zip")
    shutil.move(str(probe), config.DB_PATH)
    for n in names:
        if n.startswith("database/") or n.endswith("/"):
            continue
        target = (config.BASE_DIR / n).resolve()
        if config.BASE_DIR.resolve() not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(z.read(n))
    db.init_db()
    flash("Backup restored. A copy of your previous data was saved in the Backup folder.", "success")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------- errors
@app.errorhandler(413)
def too_large(_):
    flash(f"File too large. The limit is {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", "danger")
    return redirect(url_for("dashboard"))


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", message="That page or document was not found.", active=""), 404


@app.errorhandler(sqlite3.Error)
def db_error(e):
    return render_template("error.html", message=f"Database error: {e}", active=""), 500


@app.errorhandler(PermissionError)
def perm_error(e):
    return render_template("error.html", message="File permission error. Check that the application "
                           "folder is writable.", active=""), 500


def find_free_port(start):
    """First free port at or after ``start`` (the default may be taken by another program)."""
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            if sk.connect_ex((config.HOST, port)) != 0:  # nothing is listening there
                return port
    raise RuntimeError(f"No free port found between {start} and {start + 19}.")


def open_browser_when_ready(url, port):
    """Open Chrome only once the server really answers, so the browser never hits a dead port."""
    def _wait_and_open():
        for _ in range(100):
            try:
                with socket.create_connection((config.HOST, port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            print("Server did not start; not opening the browser.")
            return
        chrome = shutil.which("chrome") or next((p for p in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"))
            if Path(p).exists()), None)
        if chrome:
            subprocess.Popen([chrome, url])
        else:
            webbrowser.open(url)
    threading.Thread(target=_wait_and_open, daemon=True).start()


if __name__ == "__main__":
    db.init_db()
    port = find_free_port(config.PORT)
    url = f"http://{config.HOST}:{port}/"
    print("=" * 60)
    print(f"  Jayalaxmi Document System is running at:  {url}")
    print("  Keep this window open. Close it to stop the application.")
    print("=" * 60, flush=True)
    open_browser_when_ready(url, port)
    app.run(host=config.HOST, port=port, debug=False, threaded=True)
