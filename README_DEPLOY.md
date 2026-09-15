# Vyapara Track — Python + SQL + Mobile Web App

This package keeps the existing Vyapara Track UI and adds a Flask/Python backend with SQLite persistence plus PWA support.

## Local run
1. Install Python 3.11+.
2. In this folder run: `python -m pip install -r requirements.txt`
3. Run: `python app.py`
4. Open: `http://127.0.0.1:5000`

The database file `vyapara_track.db` is created automatically. The frontend also keeps a local fallback copy so the UI can recover if the server is temporarily unreachable.

## Phone use
After deploying to an HTTPS URL, open the URL on Dad's Android phone in Chrome and choose **Add to Home screen / Install app**. No APK or Play Store package is required.

## Deployment
Use a Python-capable host that supports Flask/Gunicorn and persistent SQLite storage. If the host uses ephemeral storage, SQLite data can disappear after a restart/redeploy; for real business data, use the host's persistent disk or a managed PostgreSQL database.
