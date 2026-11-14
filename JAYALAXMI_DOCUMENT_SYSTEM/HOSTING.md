# Hosting online for free (PythonAnywhere)

PythonAnywhere's free plan runs one Flask app at `https://<your-username>.pythonanywhere.com`, keeps its
files and database between visits (512 MB disk) and uses HTTPS. Free apps must be renewed once a month.

> **Privacy:** online, your customers' names, addresses, phone numbers and GST details are stored on
> PythonAnywhere's servers. The app asks for a password when hosted. Use a long one and tell no one else.

## One-time setup
1. Create a free "Beginner" account at pythonanywhere.com.
2. **Files** tab → upload `jayalaxmi_hosting.zip` (made for you in `C:\jaylaxmi`), then open a **Bash console**
   and run:
   ```
   unzip jayalaxmi_hosting.zip -d JAYALAXMI_DOCUMENT_SYSTEM
   cd JAYALAXMI_DOCUMENT_SYSTEM
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Web** tab → *Add a new web app* → *Manual configuration* → pick the newest Python offered.
4. On the Web tab set:
   - **Source code / Working directory:** `/home/<your-username>/JAYALAXMI_DOCUMENT_SYSTEM`
   - **Virtualenv:** `/home/<your-username>/JAYALAXMI_DOCUMENT_SYSTEM/venv`
   - **Static files:** URL `/static/` → directory `/home/<your-username>/JAYALAXMI_DOCUMENT_SYSTEM/static`
5. Click the **WSGI configuration file** link, delete everything in it and paste:
   ```python
   import sys
   path = "/home/<your-username>/JAYALAXMI_DOCUMENT_SYSTEM"
   if path not in sys.path:
       sys.path.insert(0, path)
   from wsgi import application
   ```
6. Edit `wsgi.py` (Files tab) and replace the `CHANGE-ME...` values with your own long password and a long
   random text for `SECRET_KEY`.
7. Web tab → **Reload**. Open `https://<your-username>.pythonanywhere.com`, sign in with your password.

## Every month
Free apps expire after a month. Open the **Web** tab and press **Run until 1 month from today**.

## Good to know
- Letters, uploaded PDFs, customers and history live on the server, separate from the copy on your PC.
  Use **Settings → Backup** regularly and keep the ZIP.
- OCR for scanned PDFs is not available online; normal E-Way Bill PDFs work.
- To update the app later, upload the changed files and press **Reload**.
