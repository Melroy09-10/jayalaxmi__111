"""Letter rendering (HTML fragments) and PDF generation."""
import io
import os
import re
from datetime import datetime
from pathlib import Path

import pymupdf
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

import config

_env = Environment(
    loader=FileSystemLoader(config.BASE_DIR / "templates" / "letters"),
    autoescape=select_autoescape(["html"]),
)
_env.filters["lines"] = lambda s: Markup("<br>".join(escape(l.strip()) for l in (s or "").splitlines() if l.strip()))

FROM_MAX_LINES = 4  # the landscape From letter has room for this many address lines at the default size
ADDRESS_MAX = 38  # characters; a longer street part is split into two lines


def address_list(addr):
    """Long address -> street part split into two near-equal lines (at a comma),
    with 'State - PINCODE' as its own last line. Returns a list of lines."""
    parts = [p.strip(" ,") for p in re.split(r"[\r\n,]+", addr or "") if p.strip(" ,")]
    if not parts:
        return []
    last = parts[-1]
    if re.fullmatch(r"\d{6}", last) and len(parts) > 1:      # '..., Karnataka, 560001'
        last = f"{parts[-2]} - {last}"
        parts = parts[:-2] + [last]
    if not re.search(r"\d{6}", last) or len(parts) < 2:        # no PIN part: leave as is
        return [", ".join(parts)]
    body = parts[:-1]
    text = ", ".join(body)
    out = [text]
    if len(text) > ADDRESS_MAX and len(body) > 1:
        best = min(range(1, len(body)), key=lambda i: abs(len(", ".join(body[:i])) - len(", ".join(body[i:]))))
        out = [", ".join(body[:best]) + ",", ", ".join(body[best:]) + ","]
    else:
        out[0] += ","
    out.append(last)
    return out


def address_three(addr, max_lines=None):
    """Address -> one line per comma-separated part (commas kept at the end of each line but the last).
    A trailing pincode is joined to the part before it, so 'State - PINCODE' stays on one line."""
    typed = [" ".join(l.split()) for l in (addr or "").splitlines() if l.strip(" ,")]
    if len(typed) > 1 and not (max_lines and len(typed) > max_lines):
        # the user broke the address into lines by hand: show it exactly like that
        return [l.rstrip(" ,") + ("," if i < len(typed) - 1 else "") for i, l in enumerate(typed)]
    parts = [p.strip(" ,") for p in re.split(r"[\r\n,]+", addr or "") if p.strip(" ,")]
    if parts and re.fullmatch(r"\d{6}", parts[-1]) and len(parts) > 1:      # '..., Karnataka, 560001'
        parts = parts[:-2] + [f"{parts[-2]} - {parts[-1]}"]
    if max_lines and len(parts) > max_lines:      # too many parts for the page: join neighbours, keeping 'State - PIN' last
        from itertools import combinations
        best = None
        for cut in combinations(range(1, len(parts)), max_lines - 1):
            idx = (0,) + cut + (len(parts),)
            groups = [", ".join(parts[idx[k]:idx[k + 1]]) for k in range(max_lines)]
            score = max(len(g) for g in groups)
            if best is None or score < best[0]:
                best = (score, groups)
        parts = best[1]
    return [p + "," for p in parts[:-1]] + parts[-1:]


def address_lines(addr):
    return Markup("<br>".join(escape(x) for x in address_list(addr)))


def title_case(text):
    """'PRABHU RESIDENCY' -> 'Prabhu Residency'; keeps 'A1/F2' and '3rd' tidy."""
    def fix(m):
        word = m.group()
        return word.lower() if m.start() and text[m.start() - 1].isdigit() else word.capitalize()
    return re.sub(r"[A-Za-z]+", fix, text or "")


_env.filters["addr"] = address_lines
_env.filters["sp"] = lambda t: Markup(re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group()), str(escape(t or ""))))


def clean_letter(raw):
    """Validate the editable letter wording; empty required lines fall back to the original text."""
    out = {}
    for k, default in config.LETTER_DEFAULTS.items():
        v = raw.get(k)
        v = default if v is None else " ".join(str(v).splitlines()).strip()[:300]
        if not v and k not in config.LETTER_OPTIONAL:
            v = default
        out[k] = v
    L = config.LOOK_DEFAULTS
    if out["lf_font"] not in config.FONT_CHOICES:
        out["lf_font"] = L["lf_font"]
    for k, lo, hi in (("lf_size_out", 7, 16), ("lf_size_from", 10, 36)):
        try:
            size = float(out[k])
            out[k] = str(size).rstrip("0").rstrip(".") if lo <= size <= hi else L[k]
        except ValueError:
            out[k] = L[k]
    for k in ("lf_weight_out", "lf_weight_from"):
        if out[k] not in ("normal", "bold"):
            out[k] = L[k]
    for k in ("lf_color_out", "lf_color_from"):
        if not _HEX.match(out[k]):
            out[k] = L[k]
    return out

LETTER_CSS = (config.BASE_DIR / "static" / "css" / "letter.css").read_text(encoding="utf-8")


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_GAPS = {"compact": (2, 35), "normal": (4, 55), "wide": (8, 80)}


def clean_format(raw):
    """Validate user-entered format settings; anything invalid falls back to the default."""
    d = config.FORMAT_DEFAULTS
    out = {}
    for k, default in d.items():
        v = " ".join(str(raw.get(k, default) if raw.get(k) is not None else default).split())[:300]
        out[k] = v
    if out["fmt_font"] not in config.FONTS:
        out["fmt_font"] = d["fmt_font"]
    if not _HEX.match(out["fmt_name_color"]):
        out["fmt_name_color"] = d["fmt_name_color"]
    for k in ("fmt_size_out", "fmt_size_from"):
        try:
            size = float(out[k])
            out[k] = str(size).rstrip("0").rstrip(".") if 9 <= size <= 36 else d[k]
        except ValueError:
            out[k] = d[k]
    if out["fmt_out_closing_align"] not in ("left", "center", "right"):
        out["fmt_out_closing_align"] = d["fmt_out_closing_align"]
    for k in ("fmt_weight_out", "fmt_weight_from"):
        if out[k] not in ("normal", "bold"):
            out[k] = d[k]
    if out["fmt_from_gap"] not in _GAPS:
        out["fmt_from_gap"] = d["fmt_from_gap"]
    if not out["fmt_out_body"]:
        out["fmt_out_body"] = d["fmt_out_body"]
    return out


def build_format(settings, parcels, amount, through):
    f = clean_format({k: settings.get(k) for k in config.FORMAT_DEFAULTS})
    body = (f["fmt_out_body"].replace("{parcels}", str(parcels)).replace("{amount}", amount)
            .replace("{through}", through))
    book_gap, from_gap = _GAPS[f["fmt_from_gap"]]
    return {**f, "body": body, "book_gap": book_gap, "from_gap": from_gap}


def _indian(n):
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def money(value):
    """73250 -> 'Rs. 73,250.00/-' (Indian digit grouping), always with two decimals."""
    whole, paise = divmod(round(float(value) * 100), 100)
    return "Rs. " + _indian(whole) + ".%02d" % paise + "/-"


def format_date(dt, fmt):
    try:
        return dt.strftime(fmt)
    except Exception:
        return dt.strftime("%d/%m/%Y")


def build_context(data, settings, when=None):
    """Snapshot everything a letter needs. Stored with the document so later
    changes to Settings never alter documents that were already generated."""
    try:   # the letter carries the E-Way Bill date, not today's date
        when = datetime.strptime(data.get("letter_date", ""), "%Y-%m-%d")
    except ValueError:
        when = when or datetime.now()
    amount = money(data["invoice_value"]) if data["invoice_value"] != "" else "Rs.________"
    through = (data["through"] or "").strip()
    parcels = int(data["parcels"])
    lt = clean_letter({k: settings.get(k) for k in config.LETTER_DEFAULTS})
    fill = lambda t: t.replace("{parcels}", str(parcels)).replace("{amount}", amount).replace("{through}", through.upper())
    body_1, body_2 = fill(lt["lt_body_1"]), fill(lt["lt_body_2"])
    if not through:   # no transporter given: "... to below address." with nothing after it
        body_1 = re.sub(r"\s*through\s*$", "", body_1).rstrip(" .") + "."
        if "{through}" in lt["lt_body_2"]:
            body_2 = ""
    if body_2 and lt["lt_body_2"].lstrip().startswith("{through}"):   # transporter continues on the same line
        body_1, body_2 = body_1.rstrip() + " " + body_2.lstrip(), ""
    k_out, k_from = float(lt["lf_size_out"]) / 10, float(lt["lf_size_from"]) / 18
    sans, serif = ("Arial, Helvetica, sans-serif", "Times New Roman, Times, serif") if lt["lf_font"] == "original" \
        else (lt["lf_font"], lt["lf_font"])
    return {
        "sz_out": {n: "%.1fpt" % (n * k_out) for n in (8, 9, 10, 11, 12, 14)},
        "sz_from": {n: "%.1fpt" % (n * k_from) for n in (14, 16, 18)},
        "ff_sans": sans, "ff_serif": serif, "wt_out": lt["lf_weight_out"], "wt_from": lt["lf_weight_from"],
        "col_out": lt["lf_color_out"], "col_from": lt["lf_color_from"], "NB": "\u00a0",
        "lt": lt, "body_1": body_1, "body_2": body_2,
        "fmt": build_format(settings, parcels, amount, through),
        "company": {k: settings.get(k, "") for k in config.DEFAULT_SETTINGS},
        "date_text": format_date(when, settings.get("date_format", "%d/%m/%Y")),
        "parcels": int(data["parcels"]),
        "invoice_value": money(data["invoice_value"]) if data["invoice_value"] != "" else "Rs.________",
        "through": (data["through"] or "").upper(),
        "to_name": data["consignee_name"] or "________",
        "to_address": data["consignee_address"] or "________",
        "to_mobile": " / ".join(p.strip() for p in re.split(r"[/,;]+", data.get("consignee_mobile") or "") if p.strip()),
        "booking": data.get("booking") or "________",
        "to_booking_out": (data.get("booking") or "").strip().upper(),
        "to_booking_from": (data.get("booking") or "").strip().upper(),
        "to_name_out": (data["consignee_name"] or "").upper() + ",",
        "to_address_out": address_three((data["consignee_address"] or "").upper()),
        "to_name_from": title_case(data["consignee_name"] or "") + ",",
        "to_address_from": address_three(title_case(data["consignee_address"] or ""), FROM_MAX_LINES),
        "ewb_no": data.get("ewb_no", ""),
        "show_ewb": settings.get("show_ewb_in_outward") == "1",
    }


def render_outward(ctx):
    return _env.get_template("outward.html").render(**ctx)


def render_from(ctx):
    return _env.get_template("from.html").render(**ctx)


def wrap_print_page(fragments, title="Letters"):
    """Standalone printable HTML: one A4 page per fragment, no app chrome."""
    return _env.get_template("print_page.html").render(fragments=fragments, title=title, css=Markup(LETTER_CSS))


def fragment_to_pdf(fragment_html):
    """Render one letter fragment to a single A4 page. Returns PDF bytes."""
    mediabox = pymupdf.paper_rect("a4-l" if "letter from" in str(fragment_html) else "a4")  # From letter prints landscape
    land = mediabox.width > mediabox.height
    # margins from the Word macros: outward 0.55in sides / 0.45in top+bottom; from (landscape) 0.75in / 0.55in
    where = mediabox + ((54, 39.6, -54, -39.6) if land else (39.6, 32.4, -39.6, -32.4))
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"   # Calibri lives here on Windows
    css, archive = LETTER_CSS, None
    if (fonts / "calibri.ttf").exists() and (fonts / "calibrib.ttf").exists():
        archive = pymupdf.Archive(str(fonts))
        css = ("@font-face{font-family:Calibri;src:url(calibri.ttf)}"
               "@font-face{font-family:Calibri;font-weight:bold;src:url(calibrib.ttf)}") + LETTER_CSS
    story = pymupdf.Story(html=str(fragment_html), user_css=css, archive=archive)
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    pages = 0
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
        if pages > 1:
            writer.close()
            raise ValueError("The letter does not fit on a single A4 page. Shorten the address.")
    writer.close()
    return stream.getvalue()


def merge_pdfs(pdf_bytes_list):
    out = pymupdf.open()
    for b in pdf_bytes_list:
        with pymupdf.open("pdf", b) as src:
            out.insert_pdf(src)
    data = out.tobytes()
    out.close()
    return data
