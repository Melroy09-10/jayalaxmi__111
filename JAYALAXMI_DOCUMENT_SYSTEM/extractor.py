"""E-Way Bill PDF text extraction (PyMuPDF, with Tesseract OCR fallback).

Nothing is invented: a field that cannot be located is returned as "" and listed
in ``missing`` so the UI can flag it for manual verification.
"""
import os
import re
import shutil

import pymupdf

import config

FIELDS = [
    "ewb_no", "ewb_date", "consignor_name", "consignor_address", "consignor_gstin",
    "consignee_name", "consignee_address", "consignee_gstin", "invoice_no", "invoice_date",
    "invoice_value", "transporter_name", "transporter_id", "transport_doc_no", "vehicle_no",
    "delivery_location", "consignee_mobile", "packages",
]

# Fields that must be present (or typed in) for the letters to be correct.
REQUIRED = ["consignee_name", "consignee_address", "invoice_value", "transporter_name"]

GSTIN_RE = r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"
VEHICLE_RE = r"\b[A-Z]{2}[ -]?\d{1,2}[ -]?[A-Z]{1,3}[ -]?\d{3,4}\b"
DATE_RE = r"\d{1,2}[/-]\d{1,2}[/-]\d{4}"
MOBILE_RE = r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)"


RECIPIENT_NAME_RE = (r"^\s*(?:Recipient|Consignee|Receiver|Buyer|Ship\s*To|Bill\s*To)\s*(?:Party\s*)?Name"
                     r"(?:\s*:\s*|\s*$)")
RECIPIENT_ADDRESS_RE = (r"^\s*(?:Recipient|Consignee|Receiver|Buyer|Ship\s*To|Bill\s*To|Delivery)\s*Address"
                        r"(?:\s*:\s*|\s*$)")
PACKAGES_RE = (r"^\s*(?:Number|No\.?)\s*of\s*(?:Packages|Parcels|Pkgs|Packets)|^\s*(?:Package|Parcel)s?\s*Count"
               r"|^\s*Total\s*(?:Packages|Parcels)")


class ExtractionError(Exception):
    """Raised with a user-readable message when a PDF cannot be processed."""


def ocr_available():
    return bool(shutil.which("tesseract") or os.environ.get("TESSDATA_PREFIX"))


def read_pdf_text(path):
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # corrupted / not a PDF
        raise ExtractionError("This file could not be opened. It is corrupted or is not a valid PDF.") from e
    with doc:
        if doc.needs_pass:
            raise ExtractionError("This PDF is password protected. Remove the password and upload it again.")
        if doc.page_count == 0:
            raise ExtractionError("This PDF has no pages.")
        parts, used_ocr = [], False
        for page in doc:
            text = page.get_text("text") or ""
            if len(text.strip()) < 25:
                try:
                    tp = page.get_textpage_ocr(language="eng", dpi=300, full=True)
                    text = page.get_text("text", textpage=tp) or ""
                    used_ocr = True
                except Exception:
                    pass
            parts.append(text)
        full = "\n".join(parts)
    if len(full.strip()) < 25:
        raise ExtractionError(
            "This PDF looks scanned (no readable text) and OCR is not available. "
            "Install Tesseract OCR (see README) or upload the original E-Way Bill PDF downloaded from the portal."
        )
    return full, used_ocr


def _clean(v):
    return re.sub(r"\s+", " ", (v or "")).strip(" :,-–")


def _lines(text):
    return [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines() if l.strip()]


def _label_value(lines, label_regex, stop_regex=None):
    """Value after a label on the same line, or on the next line(s) when the label stands alone."""
    rx = re.compile(label_regex, re.I)
    for i, line in enumerate(lines):
        m = rx.search(line)
        if not m:
            continue
        rest = _clean(line[m.end():])
        if rest:
            return rest
        for nxt in lines[i + 1:i + 3]:
            if stop_regex and re.match(stop_regex, nxt, re.I):
                break
            if nxt.rstrip().endswith(":") and len(nxt) < 45:
                break
            return _clean(nxt)
    return ""



_NOTE_RE = re.compile(r"\s*\([^)]*(?:sample|fictional|test)[^)]*\)\s*$", re.I)


def _strip_note(v):
    """Drop trailing remarks such as '(sample contact details)'."""
    return _NOTE_RE.sub("", v or "")


def _sections(lines):
    """Split a sectioned PDF ('2. From - Consignor Details', '3. To - Consignee Details', ...)
    into {'consignor': [...], 'consignee': [...], 'goods': [...], 'transport': [...]}."""
    kinds = (("consignor", r"consignor|supplier|\bfrom\b"), ("consignee", r"consignee|recipient|bill\s*to|\bto\b"),
             ("goods", r"goods|invoice\s*details|item"), ("transport", r"transport"))
    out, current = {}, None
    for line in lines:
        if re.match(r"^\d+\s*[.)]\s*\S", line) or re.search(r"\bdetails\s*$", line, re.I):
            head = re.sub(r"^\d+\s*[.)]\s*", "", line).lower()
            current = None
            for name, rx in kinds:
                if re.search(rx, head):
                    current = name
                    break
            if current:
                out.setdefault(current, [])
            continue
        if current:
            out[current].append(line)
    return out


def _gstin_and_name(value):
    m = re.search(GSTIN_RE, value or "")
    if not m:
        return "", _clean(value)
    name = _clean(_strip_note((value[m.end():]).lstrip(" ,&-:")))
    return m.group(0), ("" if name.startswith("(") else name)


def extract(path):
    """Return {'fields': {...}, 'missing': [...], 'warnings': [...], 'ocr': bool}."""
    text, used_ocr = read_pdf_text(path)
    lines = _lines(text)
    flat = "\n".join(lines)
    f = {k: "" for k in FIELDS}
    warnings = []

    # --- E-Way Bill number & date
    m = re.search(r"e-?way\s*bill\s*no\.?\s*[:\-]?\s*\n?\s*(\d{4}\s?\d{4}\s?\d{4})", flat, re.I)
    if m:
        f["ewb_no"] = m.group(1).replace(" ", "")
    m = re.search(r"generated\s*date\s*[:\-]?\s*\n?\s*(" + DATE_RE + r")", flat, re.I)
    if m:
        f["ewb_date"] = m.group(1)

    # --- Parties (portal format: "GSTIN of Supplier : <gstin>,<name>")
    sup = _label_value(lines, r"GSTIN\s*of\s*Supplier\s*:?")
    rec = _label_value(lines, r"GSTIN\s*of\s*Recipient\s*:?")
    f["consignor_gstin"], f["consignor_name"] = _gstin_and_name(sup)
    f["consignee_gstin"], f["consignee_name"] = _gstin_and_name(rec)
    if not f["consignor_gstin"] or not f["consignee_gstin"]:
        found = re.findall(GSTIN_RE, flat)
        if len(found) >= 2:
            f["consignor_gstin"] = f["consignor_gstin"] or found[0]
            f["consignee_gstin"] = f["consignee_gstin"] or found[1]
    if not f["consignee_name"]:
        for lab in (RECIPIENT_NAME_RE, r"^\s*Name\s*of\s*(?:the\s*)?(?:Recipient|Consignee|Receiver|Buyer)(?:\s*:\s*|\s*$)"):
            f["consignee_name"] = _strip_note(_label_value(lines, lab))
            if f["consignee_name"]:
                break
    if not f["consignor_name"]:
        f["consignor_name"] = _label_value(lines, r"(?:consignor|supplier)\s*(?:name)?\s*:")

    # --- Addresses / places
    dispatch = _label_value(lines, r"Place\s*of\s*Dispatch\s*:?")
    delivery = _label_value(lines, r"Place\s*of\s*Delivery\s*:?")
    f["delivery_location"] = delivery
    f["consignor_address"] = _label_value(lines, r"(?:consignor|supplier)\s*address\s*:?") or dispatch
    f["consignee_address"] = _label_value(lines, RECIPIENT_ADDRESS_RE)

    # --- Invoice
    f["invoice_no"] = _label_value(lines, r"(?:Document|Doc\.?|Invoice)\s*(?:No\.?|Number)\s*:?", r"Document\s*Date")
    m = re.search(r"(?:Document|Invoice)\s*Date\s*[:\-]?\s*\n?\s*(" + DATE_RE + r")", flat, re.I)
    if m:
        f["invoice_date"] = m.group(1)
    for lab in (r"Tot\.?\s*Inv\.?\s*Value\s*:?", r"Total\s*Invoice\s*Value\s*:?", r"Total\s*Value\s*:?",
                r"Value\s*of\s*Goods\s*:?", r"Invoice\s*Value\s*:?"):
        val = _label_value(lines, lab)
        m = re.search(r"\d[\d,]*(?:\.\d+)?", val)
        if m:
            f["invoice_value"] = m.group(0).replace(",", "")
            break

    # --- Transport
    tr = _label_value(lines, r"Transporter\s*ID\s*(?:&|and)?\s*Name\s*:?")
    if tr:
        tid, tname = _gstin_and_name(tr)
        if tid:
            f["transporter_id"], f["transporter_name"] = tid, tname
        else:
            m = re.match(r"([0-9A-Z]{15})\s*[&,]\s*(.*)", tr)
            f["transporter_id"], f["transporter_name"] = (m.group(1), _clean(m.group(2))) if m else ("", _clean(tr))
    else:
        f["transporter_name"] = _label_value(lines, r"Transporter\s*Name\s*:?")
        f["transporter_id"] = _label_value(lines, r"Transporter\s*ID\s*:?")
    if not f["transporter_name"] or not f["transporter_name"].strip("& "):
        f["transporter_name"] = ""
    td = _label_value(lines, r"Transporter\s*Doc\.?\s*No\.?\s*(?:&|and)?\s*Date\s*:?")
    f["transport_doc_no"] = _clean(re.split(r"&|\s" + DATE_RE, td)[0]) if td else ""
    m = re.search(VEHICLE_RE, flat)
    if m:
        f["vehicle_no"] = re.sub(r"[ -]", "", m.group(0))

    # --- Sectioned layout (label on one line, value on the next, under numbered headings)
    sec = _sections(lines)
    cons, sup, goods, trn = (sec.get(k, []) for k in ("consignee", "consignor", "goods", "transport"))
    if cons:
        f["consignee_name"] = _label_value(cons, r"^\s*(?:Consignee\s*)?Name\s*:?\s*") or f["consignee_name"]
        f["consignee_address"] = _label_value(cons, r"^\s*Address\s*:?\s*") or f["consignee_address"]
        f["delivery_location"] = _label_value(cons, r"^\s*Place\s*of\s*Delivery\s*:?\s*") or f["delivery_location"]
        gst = _label_value(cons, r"^\s*GSTIN\s*:?\s*")
        m = re.search(GSTIN_RE, gst)
        f["consignee_gstin"] = m.group(0) if m else f["consignee_gstin"]
        contact = _label_value(cons, r"^\s*(?:Contact|Mobile|Phone|Ph|Mob)[^\n:]*:?\s*")
        nums = re.findall(MOBILE_RE, _strip_note(contact))
        if nums:
            f["consignee_mobile"] = " / ".join(dict.fromkeys(nums))
    if sup:
        f["consignor_name"] = _label_value(sup, r"^\s*(?:Consignor\s*)?Name\s*:?\s*") or f["consignor_name"]
        f["consignor_address"] = _label_value(sup, r"^\s*Address\s*:?\s*") or f["consignor_address"]
    if goods:
        pk = _label_value(goods, r"^\s*(?:Number\s*of\s*(?:Packages|Parcels|Boxes)|No\.?\s*of\s*(?:Packages|Parcels))\s*:?\s*")
        m = re.match(r"\d+", pk)
        f["packages"] = m.group(0) if m else ""
    if trn:
        f["transporter_name"] = _label_value(trn, r"^\s*Transporter\s*Name\s*:?\s*") or f["transporter_name"]
        f["transporter_id"] = _label_value(trn, r"^\s*Transporter\s*ID\s*:?\s*") or f["transporter_id"]
    if not f["packages"]:
        pk = _label_value(lines, "(?:" + PACKAGES_RE + r")(?:\s*:\s*|\s*$)")
        m = re.match(r"\d+", pk)
        if m and int(m.group(0)) > 0:
            f["packages"] = m.group(0)
    if not f["packages"]:
        unit = _label_value(lines, r"^\s*Unit(?:\s*:\s*|\s*$)")
        qty = _label_value(lines, r"^\s*Quantity(?:\s*:\s*|\s*$)")
        m = re.match(r"\d+", qty)
        if m and int(m.group(0)) > 0 and re.search(r"parcel|package|pkg|packet|box|bag|bundle|carton|bale", unit, re.I):
            f["packages"] = m.group(0)
    if not f["ewb_no"]:
        v = _strip_note(_label_value(lines, r"e-?way\s*bill\s*(?:no\.?|number)\s*:?"))
        if len(v) >= 8 and re.search(r"\d", v):
            f["ewb_no"] = v.replace(" ", "")

    # --- Mobile fallback (unsectioned layouts): first number in the document
    if not f["consignee_mobile"]:
        m = re.search(MOBILE_RE, flat)
        if m:
            f["consignee_mobile"] = m.group(1)

    # --- Warnings
    inv_nos = {_clean(x) for x in re.findall(r"(?:Document|Invoice)\s*(?:No\.?|Number)\s*[:\-]?\s*([^\n]+)", flat, re.I)}
    inv_nos.discard("")
    if len(inv_nos) > 1:
        warnings.append("This PDF appears to contain multiple invoices. Please verify the invoice value.")
    if used_ocr:
        warnings.append("Text was read using OCR. Please check every field carefully.")
    if not f["ewb_no"]:
        warnings.append("E-Way Bill number was not found. Is this an E-Way Bill PDF?")
    if not any(f.values()):
        raise ExtractionError("No E-Way Bill information could be found in this PDF.")

    for k in f:
        f[k] = _clean(_strip_note(f[k]))
    missing = [k for k in REQUIRED if not f[k]]
    if config.DEBUG_EXTRACTION:
        print("\n" + "=" * 20 + " EXTRACTION DEBUG " + "=" * 20)
        print("RAW PDF TEXT:\n" + text)
        for label, key in (("Recipient name", "consignee_name"), ("Address", "consignee_address"),
                           ("Mobile", "consignee_mobile"), ("Package count", "packages"),
                           ("Invoice value", "invoice_value"), ("Transporter", "transporter_name"),
                           ("Place of delivery", "delivery_location")):
            print(f"{label:18}: {f[key]!r}")
        print("=" * 58, flush=True)
    return {"fields": f, "missing": missing, "warnings": warnings, "ocr": used_ocr}
