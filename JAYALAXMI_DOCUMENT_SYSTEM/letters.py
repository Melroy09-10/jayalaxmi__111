"""Letter rendering (HTML fragments) and PDF generation."""
import io
import re
from datetime import datetime

import pymupdf
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

import config

_env = Environment(
    loader=FileSystemLoader(config.BASE_DIR / "templates" / "letters"),
    autoescape=select_autoescape(["html"]),
)
_env.filters["lines"] = lambda s: Markup("<br>".join(escape(l.strip()) for l in (s or "").splitlines() if l.strip()))

LETTER_CSS = (config.BASE_DIR / "static" / "css" / "letter.css").read_text(encoding="utf-8")


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_GAPS = {"compact": (30, 50), "normal": (47, 80), "wide": (70, 110)}


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
            out[k] = str(size).rstrip("0").rstrip(".") if 9 <= size <= 18 else d[k]
        except ValueError:
            out[k] = d[k]
    if out["fmt_out_closing_align"] not in ("left", "center", "right"):
        out["fmt_out_closing_align"] = d["fmt_out_closing_align"]
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


def money(value):
    return f"Rs.{float(value):.2f}"


def format_date(dt, fmt):
    try:
        return dt.strftime(fmt)
    except Exception:
        return dt.strftime("%d/%m/%Y")


def build_context(data, settings, when=None):
    """Snapshot everything a letter needs. Stored with the document so later
    changes to Settings never alter documents that were already generated."""
    when = when or datetime.now()
    amount = money(data["invoice_value"]) if data["invoice_value"] != "" else "Rs.________"
    through = data["through"] or "________"
    parcels = int(data["parcels"])
    return {
        "fmt": build_format(settings, parcels, amount, through),
        "company": {k: settings.get(k, "") for k in config.DEFAULT_SETTINGS},
        "date_text": format_date(when, settings.get("date_format", "%d/%m/%Y")),
        "parcels": int(data["parcels"]),
        "invoice_value": money(data["invoice_value"]) if data["invoice_value"] != "" else "Rs.________",
        "through": data["through"] or "________",
        "to_name": data["consignee_name"] or "________",
        "to_address": data["consignee_address"] or "________",
        "to_mobile": data.get("consignee_mobile", ""),
        "booking": data.get("booking") or "________",
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
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 50, -54, -50)  # left, top, right, bottom margins in points
    story = pymupdf.Story(html=str(fragment_html), user_css=LETTER_CSS)
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
