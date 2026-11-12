"""End-to-end smoke test using Flask's test client. Run: venv\\Scripts\\python tests\\smoke_test.py"""
import io
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402
import extractor  # noqa: E402

tmp = Path(tempfile.mkdtemp())
sample = tmp / "sample_ewb.pdf"
subprocess.run([sys.executable, str(ROOT / "tests" / "make_sample.py"), str(sample)], check=True)

# isolate: use a throw-away database + folders
for name in ("DB_DIR", "UPLOAD_DIR", "LETTER_DIR", "CUSTOMER_DIR", "BACKUP_DIR"):
    d = tmp / name
    d.mkdir()
    setattr(config, name, d)
config.DB_PATH = config.DB_DIR / "t.db"
db.init_db()

res = extractor.extract(sample)
f = res["fields"]
print("extracted:", {k: v for k, v in f.items() if v})
assert f["ewb_no"] == "481234567890", f["ewb_no"]
assert f["consignee_name"] == "SRI RAMESH TEXTILES"
assert f["invoice_value"] == "38851.00"
assert f["transporter_name"] == "VRL LOGISTICS LTD"
assert f["vehicle_no"] == "KA20AB1234"
assert f["delivery_location"].startswith("Bangalore")

import app as appmod  # noqa: E402
import pymupdf  # noqa: E402

c = appmod.app.test_client()
home = c.get("/").data.decode()
assert "Upload E-Way Bill PDF" in home
# the old manual form must not exist anywhere
for url in ("/create", "/verify/1", "/generate", "/document/1/edit", "/document/1/parcels"):
    assert c.get(url).status_code in (404, 405), url
for word in ("Driver name", "Vehicle number", "Contents", "Parcel Dispatch", "<textarea"):
    for page in ("/", "/history", "/customers", "/settings"):
        assert word not in c.get(page).data.decode().replace("Default number of parcels", ""), (word, page)

# invalid uploads
assert c.post("/upload", data={"pdf": (io.BytesIO(b"not a pdf"), "x.pdf")}, content_type="multipart/form-data").status_code == 302
assert c.post("/upload", data={"pdf": (io.BytesIO(b"x"), "x.txt")}, content_type="multipart/form-data").status_code == 302
assert c.get("/history").data.count(b"Open / Reprint") == 0

# the whole workflow: one upload, nothing else
r = c.post("/upload", data={"pdf": (open(sample, "rb"), "sample_ewb.pdf")}, content_type="multipart/form-data")
assert r.status_code == 302 and r.headers["Location"].endswith("/document/1"), r.headers["Location"]
page = c.get("/document/1").data.decode()
assert "We herewith Sending 1 parcel containing sum of Rs.38851.00 to below address through VRL LOGISTICS LTD." in page
assert "SRI RAMESH TEXTILES" in page and "BOOKING : BANGALORE" in page
assert "Parcel count is not in the E-Way Bill" in page          # warning, not a form
assert "Place of Delivery was used" in page
body = page.split("<main")[1].split("</main>")[0]
assert body.count("<input") == 1 and 'name="mobile"' in body  # only the optional mobile box, no data-entry form
for which in ("outward", "from", "both"):
    r = c.get(f"/document/1/pdf/{which}")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    assert pymupdf.open("pdf", r.data).page_count == (2 if which == "both" else 1)
    (tmp / f"{which}.pdf").write_bytes(r.data)
assert c.get("/document/1/print/both").data.count(b'class="sheet"') == 2
assert c.get("/history").data.count(b"Open / Reprint") == 1
c.get("/document/1")
assert c.get("/history").data.count(b"Open / Reprint") == 1      # opening never duplicates history
assert c.get("/history?q=ramesh").data.count(b"Open / Reprint") == 1
assert c.get("/history?q=zzz").data.count(b"Open / Reprint") == 0

# configurable default parcels
c.post("/settings", data={"company_name": "CHANGED NAME", "default_parcels": "4"})
r = c.post("/upload", data={"pdf": (open(sample, "rb"), "again.pdf")}, content_type="multipart/form-data")
pg2 = c.get(r.headers["Location"]).data.decode()
assert "Sending 4 parcel" in pg2 and "earlier" in pg2 and "CHANGED NAME" in pg2
assert b"CHANGED NAME" not in c.get("/document/1").data.split(b"<main")[1]   # old doc unchanged

# backup & restore
r = c.post("/backup")
assert r.status_code == 200 and r.data[:2] == b"PK"
r = c.post("/restore", data={"backup": (io.BytesIO(r.data), "b.zip"), "confirm": "on"}, content_type="multipart/form-data")
assert r.status_code == 302
assert c.post("/restore", data={"backup": (io.BytesIO(b"junk"), "b.zip"), "confirm": "on"}, content_type="multipart/form-data").status_code == 302
from app import clean_mobile
assert clean_mobile("9845100490 / 6362226302") == ("9845100490 / 6362226302", None) and clean_mobile("12345")[1]

# a PDF missing details: warnings, no form, nothing invented
import pymupdf as m
d = m.open()
pg = d.new_page()
pg.insert_text((50, 80), "E-WAY BILL", fontsize=10)
pg.insert_text((50, 100), "eWay Bill No: 5011 2233 4455", fontsize=10)
pg.insert_text((50, 120), "Something else entirely here for text", fontsize=10)
(tmp / "thin.pdf").write_bytes(d.tobytes())
r = c.post("/upload", data={"pdf": (open(tmp / "thin.pdf", "rb"), "thin.pdf")}, content_type="multipart/form-data")
thin = c.get(r.headers["Location"]).data.decode()
assert "Consignee name was not found" in thin and "Invoice value was not found" in thin and "________" in thin
# the user's sectioned-layout sample: name, address, both mobiles, packages, transporter
real = ROOT / "tests" / "sample_eway_bill_for_testing.pdf"
r = c.post("/upload", data={"pdf": (open(real, "rb"), real.name)}, content_type="multipart/form-data")
pg = c.get(r.headers["Location"]).data.decode()
for needle in ("MINAXI SILK HOUSE", "#9, Honurappa Lane", "Mob: 9845100490 / 6362226302", "Rs.38851.00",
               "through Vishal", "BOOKING : BENGALURU", "PH 9845100490 / 6362226302", "Sending 1 parcel"):
    assert needle in pg, needle
assert "name was not found" not in pg and "Parcel count is not" not in pg and "Place of Delivery was used" not in pg
# compact layout: "Recipient Name" / "Recipient Address" labels, GSTIN line carries only a remark
comp = ROOT / "tests" / "compact_sample_eway_bill.pdf"
r = c.post("/upload", data={"pdf": (open(comp, "rb"), comp.name)}, content_type="multipart/form-data")
pg = c.get(r.headers["Location"]).data.decode()
assert pg.count("MINAXI SILK HOUSE") >= 2, "name must appear in both letters"
assert "#9, Honurappa Lane" in pg and "through Vishal" in pg and "Rs.38851.00" in pg
assert "name was not found" not in pg
# this PDF has no package field, so the Settings default (4 at this point) is used and the user is told
assert "Parcel count is not in the E-Way Bill" in pg

# explicit package counts must be picked up (several label styles, and quantity+unit)
def synthetic(extra_lines):
    d = pymupdf.open()
    pg = d.new_page()
    y = 60
    for ln in ["E-Way Bill No:", "TEST-0001-0002", "GSTIN of Recipient", "29PQRSX5678L1Z2 (TEST GSTIN)",
               "Recipient Name", "ACME SILKS", "Recipient Address", "12 Main Road, Mysuru - 570001",
               "Place of Delivery", "Mysuru, Karnataka", "Value of Goods", "1500.00",
               "Transporter Name", "Ravi"] + extra_lines:
        pg.insert_text((50, y), ln, fontsize=10)
        y += 16
    p = tmp / f"syn{abs(hash(tuple(extra_lines)))}.pdf"
    p.write_bytes(d.tobytes())
    return p

for extra, expect in ((["Number of Packages", "5"], 5), (["No. of Packages: 7"], 7),
                      (["Package Count", "3"], 3), (["Quantity", "6", "Unit", "Parcel"], 6),
                      (["Quantity", "6", "Unit", "Kilograms"], None)):
    fields = extractor.extract(synthetic(extra))["fields"]
    assert fields["consignee_name"] == "ACME SILKS", fields["consignee_name"]
    assert fields["packages"] == (str(expect) if expect else ""), (extra, fields["packages"])
r = c.post("/upload", data={"pdf": (open(synthetic(["Number of Packages", "5"]), "rb"), "five.pdf")},
           content_type="multipart/form-data")
pg = c.get(r.headers["Location"]).data.decode()
assert "Sending 5 parcel" in pg and "Parcel count is not" not in pg and pg.count("ACME SILKS") >= 2
# adding a mobile number to an existing document updates both letters
doc_id = r.headers["Location"].rsplit("/", 1)[1]
assert "Mob:" not in pg
assert c.post(f"/document/{doc_id}/mobile", data={"mobile": "123"}).status_code == 302
assert "Mob:" not in c.get(f"/document/{doc_id}").data.decode()
c.post(f"/document/{doc_id}/mobile", data={"mobile": "98451 00490, 6362226302"})
pg = c.get(f"/document/{doc_id}").data.decode()
assert "Mob: 9845100490 / 6362226302" in pg and "PH 9845100490 / 6362226302" in pg
assert "No consignee mobile number was found" not in pg
for which in ("outward", "from"):
    txt = pymupdf.open("pdf", c.get(f"/document/{doc_id}/pdf/{which}").data)[0].get_text()
    assert "9845100490 / 6362226302" in txt, which
print("ALL OK")
