"""WSGI entry point for hosting (e.g. PythonAnywhere). See HOSTING.md.

Edit the three values below once on the server. They are NOT needed for local use (start.bat).
"""
import os

os.environ.setdefault("APP_PASSWORD", "CHANGE-ME-to-a-long-password")  # required online: login password
os.environ.setdefault("SECRET_KEY", "CHANGE-ME-to-a-long-random-text")  # signs the login cookie
os.environ.setdefault("HTTPS_ONLY", "1")                                  # cookie only over https
os.environ.setdefault("JAYALAXMI_DEBUG", "0")                             # do not print PDF contents in logs

from app import app as application  # noqa: E402,F401
