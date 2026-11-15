"""Paths and default configuration for the Jayalaxmi Document System."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
UPLOAD_DIR = BASE_DIR / "Uploaded_PDFs"
LETTER_DIR = BASE_DIR / "Generated_Letters"
CUSTOMER_DIR = BASE_DIR / "Customer_Records"
BACKUP_DIR = BASE_DIR / "Backup"
DB_PATH = DB_DIR / "jayalaxmi.db"

# Temporary: print the raw PDF text and extracted fields to the console on every upload.
DEBUG_EXTRACTION = os.environ.get("JAYALAXMI_DEBUG", "1") == "1"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
HOST = "127.0.0.1"
PORT = 5050

# Seeded into the settings table on first run. Editable afterwards via Settings only.
DEFAULT_SETTINGS = {
    "company_name": "JAYALAXMI SILKS PRIVATE LIMITED",
    "company_address_1": "D No 11-3-136, 11-3-136(1,3-19), NH 169A,",
    "company_address_2": "Bannanje, Udupi, Udupi Taluk & District \u2013 576 101, Karnataka",
    "company_email": "jayalaxmisilks@gmail.com",
    "company_mobile": "+91 8861844322",
    "company_cin": "U47711KA2026PTC217940",
    "from_line_1": "JAYALAXMI SILKS PVT LTD",
    "from_line_2": "BANNANJE",
    "from_line_3": "UDUPI - 576101",
    "from_line_4": "MOB \u2013 8861844322",
    "date_format": "%d/%m/%Y",
    "paper_size": "A4",
    "output_folder": "",
    "show_ewb_in_outward": "0",
    "default_parcels": "1",
}

# Letter format (editable on the "Letter Format" page). Stored in the settings table.
FORMAT_DEFAULTS = {
    "fmt_font": "Times New Roman, serif",
    "fmt_size_out": "12.5",
    "fmt_size_from": "27",
    "fmt_weight_out": "normal",
    "fmt_weight_from": "bold",
    "fmt_name_color": "#1F3A5F",
    "fmt_out_salutation": "To whomsoever it may concern",
    "fmt_out_body": "We herewith Sending {parcels} parcel containing sum of {amount} to below address through {through}.",
    "fmt_out_to_label": "TO",
    "fmt_out_mob_label": "Mob:",
    "fmt_out_note": "This does not contain any commercial value.",
    "fmt_out_closing": "Thanking You,",
    "fmt_out_closing_align": "right",
    "fmt_from_to": "To",
    "fmt_from_ph": "PH",
    "fmt_from_booking": "BOOKING :",
    "fmt_from_from": "From",
    "fmt_from_gap": "normal",
}
FONTS = {
    "Times New Roman, serif": "Times New Roman",
    "Arial, Helvetica, sans-serif": "Arial",
    "Courier New, monospace": "Courier New",
}
# Editable letter text (Letter Format page). Defaults are exactly the wording of the Word macros.
LETTER_DEFAULTS = {
    "lt_company": "JAYALAXMI SILKS PRIVATE LIMITED",
    "lt_addr_1": "D No 11-3-136, 11-3-136(1,3-19), NH 169A,",
    "lt_addr_2": "Bappanadu, Mulki, Udupi Taluk & District - 576 101, Karnataka",
    "lt_contact": "E Mail ID: jayalaxmisilks@gmail.com     Mobile No: +91 8861844322",
    "lt_cin": "CIN: U47711KA2026PTC217940",
    "lt_salutation": "To whomsoever it may concern",
    "lt_body_1": "We herewith Sending {parcels} parcel containing sum of {amount} to below address through",
    "lt_body_2": "{through}.",
    "lt_to": "To,",
    "lt_ph": "Ph:",
    "lt_closing": "Thanking You,",
    "lt_from_to": "To",
    "lt_from_from": "FROM",
    "lt_from_1": "JAYALAXMI SILKS PVT LTD",
    "lt_from_2": "BANNANJE",
    "lt_from_3": "UDUPI - 576101",
    "lt_from_4": "MOB - 8861844322",
}
LOOK_DEFAULTS = {
    "lf_font": "original",        # 'original' = Arial + Times New Roman exactly as in the Word macros
    "lf_size_out": "10",          # base size of the Outward letter body text (other sizes scale with it)
    "lf_size_from": "18",         # base size of the From letter receiver text
    "lf_weight_out": "bold",
    "lf_weight_from": "bold",
    "lf_color_out": "#808080",
    "lf_color_from": "#000000",
}
LETTER_DEFAULTS.update(LOOK_DEFAULTS)
FONT_CHOICES = {
    "original": "Original (Arial + Times New Roman)",
    "Arial, Helvetica, sans-serif": "Arial",
    "Times New Roman, Times, serif": "Times New Roman",
    "Courier New, monospace": "Courier New",
}
LETTER_OPTIONAL = {"lt_salutation", "lt_closing", "lt_from_4", "lt_body_2"}  # may be left empty to drop the line
DEFAULT_SETTINGS.update(FORMAT_DEFAULTS)
DEFAULT_SETTINGS.update(LETTER_DEFAULTS)

DATE_FORMATS = {
    "%d/%m/%Y": "DD/MM/YYYY",
    "%d-%m-%Y": "DD-MM-YYYY",
    "%d %b %Y": "DD Mon YYYY",
    "%d %B %Y": "DD Month YYYY",
    "%Y-%m-%d": "YYYY-MM-DD",
}

for _d in (DB_DIR, UPLOAD_DIR, LETTER_DIR, CUSTOMER_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)
