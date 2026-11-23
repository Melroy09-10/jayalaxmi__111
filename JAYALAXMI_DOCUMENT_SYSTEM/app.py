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
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES * 10  # several E-Way Bills per upload


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


@app.after_request
def disable_autofill(resp):
    """Server-side backstop: every form/input/textarea/select in every HTML page gets autocomplete off."""
    if resp.mimetype != "text/html" or resp.direct_passthrough:
        return resp
    html = resp.get_data(as_text=True)

    def tag(m):
        t = m.group(0)
        if re.search(r"\bautocomplete\s*=", t, re.I) or re.search(r"""type\s*=\s*["']?(?:hidden|file)""", t, re.I):
            return t
        val = "new-password" if re.search(r"""type\s*=\s*["']?password""", t, re.I) else "off"
        return t[:-1].rstrip("/").rstrip() + f' autocomplete="{val}"' + ("/>" if t.endswith("/>") else ">")

    resp.set_data(re.sub(r"<(?:form|input|textarea|select)\b[^>]*>", tag, html, flags=re.I))
    return resp


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


MOBILE_OK = re.compile(r"^\d{10}$")


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
    good = []
    for p in parts:
        digits = re.sub(r"[\s\-()]", "", p)
        digits = re.sub(r"^(\+?91|0)(?=\d{10}$)", "", digits)
        if not MOBILE_OK.match(digits):
            return ", ".join(good), "Each mobile number must be exactly 10 digits (separate several numbers with /)."
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


def build_documents(data, when=None, over=None):
    settings = {**db.get_settings(), **(over or data.get("lt_over") or {})}
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


def ingest_pdf(f):
    """Validate, store and extract one uploaded E-Way Bill. Returns (upload_id, error message or None)."""
    name = f.filename or "file"
    if not name.lower().endswith(".pdf"):
        return None, f"{name}: only PDF files can be uploaded."
    blob = f.read()
    if not blob.startswith(b"%PDF"):
        return None, f"{name}: this file is not a valid PDF."
    original = secure_filename(name) or "ewaybill.pdf"
    stored = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{original}"
    dest = config.UPLOAD_DIR / stored
    try:
        dest.write_bytes(blob)
    except OSError as e:
        return None, f"{name}: could not save the file ({e.strerror or e}). Check folder permissions."
    try:
        result = extractor.extract(dest)
    except extractor.ExtractionError as e:
        dest.unlink(missing_ok=True)
        return None, f"{name}: {e}"
    except Exception as e:  # unexpected parser failure
        dest.unlink(missing_ok=True)
        return None, f"{name}: unexpected error while reading the PDF: {e}"
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO uploads(original_name,stored_name,sha256,ewb_no,extracted_json) VALUES(?,?,?,?,?)",
            (name, stored, hashlib.sha256(blob).hexdigest(), result["fields"]["ewb_no"], json.dumps(result)))
    return cur.lastrowid, None


@app.route("/upload", methods=["POST"])
def upload():
    """Upload one or more E-Way Bills -> extract each -> one combined details form."""
    files = [f for f in request.files.getlist("pdf") if f and f.filename]
    if not files:
        return upload_error("Please choose at least one E-Way Bill PDF to upload.")
    ids = []
    for f in files:
        up_id, err = ingest_pdf(f)
        if err:
            flash(err, "danger")
        else:
            ids.append(up_id)
    if not ids:
        return redirect(url_for("dashboard"))
    if len(ids) > 1:
        err = party_mismatch(load_uploads(ids))
        if err:
            flash(err, "danger")
            return redirect(url_for("dashboard"))
    if len(ids) == 1:
        return redirect(url_for("complete_upload", upload_id=ids[0]))
    return redirect(url_for("complete_uploads", ids=",".join(map(str, ids))))


def load_uploads(ids):
    with db.connect() as c:
        rows = [c.execute("SELECT * FROM uploads WHERE id=?", (i,)).fetchone() for i in ids]
    if not rows or any(r is None for r in rows):
        abort(404)
    return rows


def merged_auto(ups):
    """Combine the extracted details of several E-Way Bills into one set of letter data:
    parcels and amounts are added up; date, party, transporter come from the first bill that has them;
    phone numbers are collected without repeats. Also returns one summary row per bill."""
    autos, bills = [], []
    for up in ups:
        a = auto_data(json.loads(up["extracted_json"]))
        autos.append(a)
        bills.append({"file": up["original_name"], "ewb_no": a["ewb_no"], "doc_no": a["doc_no"], "date": a["letter_date"],
                      "party": a["consignee_name"], "parcels": a["parcels"], "value": a["invoice_value"]})
    first = lambda k: next((a[k] for a in autos if a[k]), "")
    phones = []
    for a in autos:
        for p in re.split(r"[/,;]+", a["consignee_mobile"]):
            if p.strip() and p.strip() not in phones:
                phones.append(p.strip())
    amounts = [a["invoice_value"] for a in autos if a["invoice_value"] != ""]
    warnings = []
    for up, a in zip(ups, autos):
        for w in json.loads(up["extracted_json"])["warnings"] + a["warnings"]:
            w = f"{up['original_name']}: {w}" if len(ups) > 1 else w
            if w not in warnings:
                warnings.append(w)
    data = {
        "letter_date": first("letter_date"), "parcels": sum(a["parcels"] for a in autos),
        "consignee_name": first("consignee_name"), "consignee_address": first("consignee_address"),
        "consignee_mobile": ", ".join(phones), "booking": first("booking"), "through": first("through"),
        "ewb_no": ", ".join(dict.fromkeys(a["ewb_no"] for a in autos if a["ewb_no"])),
        "doc_no": ", ".join(dict.fromkeys(a["doc_no"] for a in autos if a["doc_no"])),
        "invoice_no": first("invoice_no"), "invoice_value": round(sum(amounts), 2) if amounts else "",
        "warnings": warnings, "_address_from_place": any(a["_address_from_place"] for a in autos),
    }
    return data, phones, bills


def party_mismatch(ups):
    """Error text when the E-Way Bills do not all name exactly the same party, else None."""
    names = [(up["original_name"], auto_data(json.loads(up["extracted_json"]))["consignee_name"]) for up in ups]
    names = [(f, n) for f, n in names if n]
    if len({" ".join(n.upper().split()) for _, n in names}) > 1:
        return "Party names are different. Please check."
    return None


def finish_upload(ups, data):
    """Build the letters from the (user-confirmed) merged data."""
    data["warnings"] = [w for w in data["warnings"] if "Parcel count" not in w and "mobile" not in w.lower()]
    with db.connect() as c:
        for up in ups:
            dup = up["ewb_no"] and c.execute("SELECT id FROM documents WHERE ewb_no LIKE ? LIMIT 1",
                                             (f"%{up['ewb_no']}%",)).fetchone()
            if dup:
                data["warnings"].insert(0, f"Letters were already generated earlier for E-Way Bill {up['ewb_no']} "
                                           f"(document #{dup['id']}).")
                break
    try:
        doc_id, warn = save_document(ups, data)
    except (ValueError, sqlite3.Error, OSError) as e:
        return upload_error(f"Could not generate the letters: {e}")
    if warn:
        flash(warn, "warning")
    return redirect(url_for("view_document", doc_id=doc_id))


def parse_form_date(text):
    """'15/11/2026' (also - or . separators) -> datetime, or None."""
    m = re.fullmatch(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*", text or "")
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    except ValueError:
        return None


@app.route("/upload/<int:upload_id>/complete", methods=["GET", "POST"])
def complete_upload(upload_id):
    return details_form([upload_id])


@app.route("/uploads/complete", methods=["GET", "POST"])
def complete_uploads():
    try:
        ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip()]
    except ValueError:
        abort(404)
    return details_form(ids)


SKIPPABLE = {"consignee_name", "consignee_address", "through", "invoice_value", "phones"}


def details_form(ids):
    """Show everything read from the E-Way Bill(s) in one editable form before the letters are made."""
    ups = load_uploads(ids)
    err = party_mismatch(ups)
    if err:
        flash(err, "danger")
        return redirect(url_for("dashboard"))
    auto, phones, bills = merged_auto(ups)
    d = datetime.strptime(auto["letter_date"], "%Y-%m-%d") if auto["letter_date"] else None
    values = {
        "consignee_name": auto["consignee_name"], "consignee_address": auto["consignee_address"],
        "letter_date": d.strftime("%d/%m/%Y") if d else "", "parcels": auto["parcels"],
        "invoice_value": "%.2f" % auto["invoice_value"] if auto["invoice_value"] != "" else "",
        "through": auto["through"], "phones": phones, "booking": "",
    }
    errors, skips = {}, set()
    if request.method == "POST":
        f = request.form
        clean = lambda k: " ".join(f.get(k, "").split())
        # every field must be filled, unless the user ticks "Not needed" for it
        skips = {k for k in SKIPPABLE if f.get("skip_" + k)}
        values.update(consignee_name=clean("consignee_name"), letter_date=clean("letter_date"),
                      parcels=clean("parcels"), invoice_value=clean("invoice_value"), through=clean("through"), booking=clean("booking"),
                      consignee_address="\n".join(l.strip() for l in f.get("consignee_address", "").splitlines() if l.strip()))
        values["phones"] = [p.strip() for p in f.getlist("phone") if p.strip()]
        for k in skips:
            values[k] = [] if k == "phones" else ""
        out = {}
        fill = "Please fill this in, or tick 'Not needed'."
        for k, msg in (("consignee_name", fill), ("consignee_address", fill), ("through", fill)):
            if k in skips:
                out[k] = ""
            elif values[k]:
                out[k] = values[k]
            else:
                errors[k] = msg
        values["booking"] = values["booking"].upper()
        out["booking"] = values["booking"]   # optional: typed by the user, left out of the letters if empty
        dt = parse_form_date(values["letter_date"])
        if dt:
            out["letter_date"] = dt.strftime("%Y-%m-%d")
        else:
            errors["letter_date"] = "Enter the date as DD/MM/YYYY, e.g. 15/11/2026"
        if values["parcels"].isdigit() and int(values["parcels"]) > 0:
            out["parcels"] = int(values["parcels"])
        else:
            errors["parcels"] = "Enter a whole number, e.g. 5"
        if "invoice_value" in skips:
            out["invoice_value"] = ""
        elif not values["invoice_value"]:
            errors["invoice_value"] = fill
        else:
            try:
                out["invoice_value"] = round(float(values["invoice_value"].replace(",", "")), 2)
                if out["invoice_value"] <= 0:
                    raise ValueError
            except ValueError:
                errors["invoice_value"] = "Enter the amount as a number, e.g. 157710.00"
        cleaned = []
        for p in values["phones"]:
            digits = re.sub(r"[\s\-()]", "", p)
            if not MOBILE_OK.match(digits):
                errors["phones"] = f"'{p}' is not valid. Each phone number must contain exactly 10 digits."
                break
            if digits not in cleaned:
                cleaned.append(digits)
        if "phones" not in skips and not values["phones"]:
            errors["phones"] = "Add a phone number, or tick 'Not needed'."
        out["consignee_mobile"] = ", ".join(cleaned)
        if not errors:
            auto.update(out)
            return finish_upload(ups, auto)
    return render_template("complete.html", ups=ups, bills=bills, errors=errors, values=values, skips=skips, active="dashboard")


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
    # name only: drop any 15-character GSTIN-style transporter ID (e.g. 29ABKCS4159A1Z8)
    through = re.sub(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]", "", fields.get("transporter_name", ""))
    through = through.strip(" &,-:")
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
        "through": through, "ewb_no": ewb_of(fields), "doc_no": fields.get("invoice_no", "") or fields.get("transport_doc_no", ""), "invoice_no": fields.get("invoice_no", ""),
        "invoice_value": value, "warnings": warnings, "missing": missing, "_address_from_place": from_place,
    }


def ewb_of(fields):
    return fields.get("ewb_no", "")


def save_document(ups, data):
    """Render both letters and store them. ``ups`` are the uploaded E-Way Bills behind this document.
    Returns (doc_id, warning)."""
    up = ups[0]
    data["upload_ids"] = [u["id"] for u in ups]
    ctx, outward_html, from_html = build_documents(data)
    snapshot = json.dumps({**data, "ctx": ctx})
    with db.connect() as c:
        cur = c.execute("""INSERT INTO documents(upload_id,ewb_no,receiver_name,invoice_value,parcels,
                           original_pdf,data_json,outward_html,from_html) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (up["id"], data["ewb_no"], data["consignee_name"] or "(name not found)",
                         data["invoice_value"] or None, int(data["parcels"]), ", ".join(u["original_name"] for u in ups),
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
    doc_no = json.loads(doc["data_json"]).get("doc_no", "")
    return render_template("preview.html", doc=doc, doc_no=doc_no, warnings=warnings, mobile=mobile, active="dashboard")


def copy_count(name):
    """How many copies of a letter to print (1-50), from the ?from_n= / ?out_n= query values."""
    try:
        return min(50, max(1, int(request.args.get(name, 1))))
    except ValueError:
        return 1


def fragments_for(doc, which):
    n_from, n_out = copy_count("from_n"), copy_count("out_n")
    if which == "outward":
        return [doc["outward_html"]] * n_out
    if which == "from":
        return [doc["from_html"]] * n_from
    if which == "both":
        return [doc["from_html"]] * n_from + [doc["outward_html"]] * n_out
    abort(404)


@app.route("/document/<int:doc_id>/format", methods=["GET", "POST"])
def edit_document_format(doc_id):
    """Edit the look and wording of this one document's letters, with a live preview of its real data.
    Saving regenerates just this document; optionally also makes it the default for new letters."""
    doc = load_doc(doc_id)
    data = {k: v for k, v in json.loads(doc["data_json"]).items() if k != "ctx"}
    snapshot = json.loads(doc["data_json"]).get("ctx", {}).get("lt") or {}
    when = datetime.fromisoformat(doc["created_at"])
    if request.method == "POST":
        if request.form.get("action") == "reset":
            over = dict(config.LETTER_DEFAULTS)
        else:
            over = letters.clean_letter(request.form)
        data["lt_over"] = over
        ctx, outward_html, from_html = build_documents(data, when, over)
        try:
            with db.connect() as c:
                c.execute("""UPDATE documents SET data_json=?,outward_html=?,from_html=?,
                             updated_at=datetime('now','localtime') WHERE id=?""",
                          (json.dumps({**data, "ctx": ctx}), outward_html, from_html, doc_id))
        except (ValueError, sqlite3.Error, OSError) as e:
            flash(f"Could not update the letters: {e}", "danger")
            return redirect(url_for("edit_document_format", doc_id=doc_id))
        if request.form.get("as_default") and request.form.get("action") != "reset":
            db.save_settings(over)
        flash("Letters updated." + (" Also saved as the default for new letters." if request.form.get("as_default") and request.form.get("action") != "reset" else ""), "success")
        return redirect(url_for("view_document", doc_id=doc_id))
    current = {**db.get_settings(), **snapshot, **(data.get("lt_over") or {})}
    vals = letters.clean_letter({k: current.get(k) for k in config.LETTER_DEFAULTS})
    return render_template("format.html", f=vals, fonts=config.FONT_CHOICES, active="dashboard", doc=doc,
                           preview_url=url_for("document_format_preview", doc_id=doc_id))


@app.route("/document/<int:doc_id>/format/preview", methods=["POST"])
def document_format_preview(doc_id):
    doc = load_doc(doc_id)
    data = {k: v for k, v in json.loads(doc["data_json"]).items() if k != "ctx"}
    over = letters.clean_letter(request.form)
    _, outward_html, from_html = build_documents(data, datetime.fromisoformat(doc["created_at"]), over)
    return {"outward": outward_html, "from": from_html}



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
            parts = ([letters.fragment_to_pdf(doc["from_html"])] * copy_count("from_n")
                     + [letters.fragment_to_pdf(doc["outward_html"])] * copy_count("out_n"))
            ids = json.loads(doc["data_json"]).get("upload_ids") or [doc["upload_id"]]
            for uid in ids:
                with db.connect() as c:
                    up = c.execute("SELECT stored_name FROM uploads WHERE id=?", (uid,)).fetchone()
                original = safe_child(config.UPLOAD_DIR, up["stored_name"]) if up else None
                if not original or not original.exists():
                    flash("An original E-Way Bill file is no longer available, so all of them cannot be printed.", "danger")
                    return redirect(url_for("view_document", doc_id=doc_id))
                parts.append(original.read_bytes())
            blob = letters.merge_pdfs(parts)
        else:
            pages = {}   # render each distinct letter once, then repeat it for the copies
            blob = letters.merge_pdfs([pages.setdefault(f, letters.fragment_to_pdf(f)) for f in fragments_for(doc, which)])
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
