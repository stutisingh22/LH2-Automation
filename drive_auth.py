#!/usr/bin/env python3
"""
Run once from the project folder to sign in to Google Drive and create/refresh token.json.
This grants Drive & Docs read+write so "Save to Drive" works in the app.

  cd /path/to/LH2_Automation
  python drive_auth.py   # or: python3 drive_auth.py

Requires: credentials.json in this folder (from Google Cloud Console OAuth client).
Use the same Python environment as your app (e.g. activate your venv first so
google-auth, google-auth-oauthlib, google-api-python-client are available).

A browser window will open for you to sign in. After that, token.json is saved
and the app can use it (no need to run again unless you revoke access or add new scopes).
"""

import os
import sys

# Run from project root so token.json is created here
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)

def main():
    from drive_sync import _get_oauth_creds, _drive, _docs
    print("Google Drive sign-in — a browser window will open.")
    creds = _get_oauth_creds()
    if not creds:
        print("Failed to get credentials. Ensure credentials.json is in this folder.")
        sys.exit(1)
    # Trigger Drive and Docs so they're initialized with the new token
    _drive()
    _docs()
    print("Done. token.json has been created/updated. You can run your app now.")

if __name__ == "__main__":
    main()
