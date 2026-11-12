# JAYALAXMI SILKS – Document Management System

A local Windows web app. Upload one E-Way Bill PDF and it automatically reads the details and generates two
print-ready A4 letters (Outward Letter and From Letter). There is no data-entry form. Everything stays on this PC.

## Install (once)
1. Install **Python 3.10 or newer** from python.org (tick *Add python.exe to PATH*).
2. Double-click **start.bat**. The first run creates a virtual environment and installs Flask and PyMuPDF
   (internet needed only for this step).
3. Optional – scanned PDFs: install **Tesseract OCR** (UB Mannheim Windows build) and make sure
   `tesseract.exe` is on PATH. Without it, text-based PDFs from the E-Way Bill portal work normally;
   scanned ones show a clear message.

## Daily use
1. Double-click **start.bat** – Chrome opens at http://127.0.0.1:5050 (keep the black window open).
2. On the Dashboard click **Upload E-Way Bill PDF** and choose the file (or drag it in).
3. Both letters are generated and previewed. Use **Print Outward / From / Both** (opens the Chrome print dialog;
   set Margins to None and untick Headers and footers) or **Download PDF**.
4. Anything not found in the PDF is listed as a warning above the previews and shown as `________` in the
   letter. Nothing is invented. The parcel count is not in an E-Way Bill, so a default is used
   (Settings → Default number of parcels, initially 1).

## Folders
| Folder | Contents |
|---|---|
| `database/` | SQLite database |
| `Uploaded_PDFs/` | Original E-Way Bill PDFs |
| `Generated_Letters/` | Generated letter PDFs (never overwritten) |
| `Customer_Records/` | `customers.csv` mirror of saved customers |
| `Backup/` | Backup ZIPs |

## Notes
- Company details and the default parcel count live in **Settings**. Changes affect new letters only.
- The portal PDF has only the *place* of delivery, not the full address or mobile. The place is used (with a
  warning). Receivers whose full details appeared in a PDF are saved and reused automatically next time.
- Opening or reprinting an old document never creates a new history entry.
- The server listens on 127.0.0.1 only. Uploads: PDF only, 15 MB max.

## Tests
`venv\Scripts\python tests\smoke_test.py` – uploads a sample E-Way Bill and checks the whole flow, including
that no manual form exists.
