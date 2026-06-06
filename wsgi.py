"""WSGI entry point for production deployment (Render, Heroku, etc.).

This file lives at the repo root so that ``gunicorn wsgi:app`` works
out-of-the-box.  It adds ``skylink_app/`` to the Python path so that
the Flask app's internal imports (``from db import …``) resolve correctly.
"""
import os
import sys

# Ensure the skylink_app package is importable.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "skylink_app"))

from app import app  # noqa: E402

if __name__ == "__main__":
    app.run()