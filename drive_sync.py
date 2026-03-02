"""
drive_sync.py — Google Drive integration using OAuth

Uses credentials.json + token.json (same as notion_research_bot.py).
Falls back to service account if GOOGLE_SERVICE_ACCOUNT_JSON is set.

FIX: Now supports OAuth flow matching your existing research bot setup.
"""

import io
import json
import os
import logging
from datetime import datetime

from config import GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_DRIVE_FOLDER_ID, MAIN_FOLDER_ID, CONTEXT_FOLDER_ID, MAX_DRIVE_TOKENS
from db_ops import get_cached_context, save_cached_context, delete_cached_contexts_with_prefix

log = logging.getLogger(__name__)

_drive_svc = None
_docs_svc = None
_file_cache = {}
_company_context_cache = {}

# Path to OAuth files — look in the same directory as this file, then cwd
_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _first_exists(*paths):
    """Return the first path that exists, or the first path if none exist."""
    for p in paths:
        if p and os.path.exists(p):
            return p
    return paths[0] if paths else None


def _creds_file():
    return _first_exists(
        os.path.join(_APP_DIR, "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
    )


def _token_file():
    return _first_exists(
        os.path.join(_APP_DIR, "token.json"),
        os.path.join(os.getcwd(), "token.json"),
    )


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def _get_oauth_creds():
    """Get OAuth credentials from token.json, refreshing if needed. Accepts any existing token (read-only is ok for init); write will 403 until re-auth."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        _TOKEN_FILE = _token_file()
        _CREDS_FILE = _creds_file()
        if not _TOKEN_FILE and not _CREDS_FILE:
            return None

        creds = None
        if _TOKEN_FILE and os.path.exists(_TOKEN_FILE):
            # Load with scopes=None so we accept token created with any scopes (e.g. readonly). Write will 403 until user re-auths.
            try:
                creds = Credentials.from_authorized_user_file(_TOKEN_FILE, SCOPES)
            except Exception:
                creds = Credentials.from_authorized_user_file(_TOKEN_FILE, None)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    if _TOKEN_FILE:
                        with open(_TOKEN_FILE, "w") as f:
                            f.write(creds.to_json())
                except Exception as e:
                    log.warning("Token refresh failed: %s", e)
                    creds = None
            if (not creds or not creds.valid) and _CREDS_FILE:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(_CREDS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                _TOKEN_FILE = _token_file() or os.path.join(_APP_DIR, "token.json")
                with open(_TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
            elif not creds or not creds.valid:
                return None

        return creds
    except Exception as e:
        log.error("OAuth credential error: %s", e)
        return None


def _get_service_account_creds():
    """Get service account credentials from env var."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return None
    try:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_info(
            json.loads(GOOGLE_SERVICE_ACCOUNT_JSON),
            scopes=SCOPES,
        )
    except Exception as e:
        log.error("Service account credential error: %s", e)
        return None


def _get_creds():
    """Try OAuth first, then service account."""
    if _token_file() or _creds_file():
        creds = _get_oauth_creds()
        if creds:
            return creds

    return _get_service_account_creds()


def _drive():
    global _drive_svc
    if _drive_svc:
        return _drive_svc
    creds = _get_creds()
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build
        _drive_svc = build("drive", "v3", credentials=creds)
        log.info("Drive service initialized (OAuth)" if _token_file() else "Drive service initialized (service account)")
        return _drive_svc
    except Exception as e:
        log.error("Failed to init Drive service: %s", e)
        return None


def _docs():
    global _docs_svc
    if _docs_svc:
        return _docs_svc
    creds = _get_creds()
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build
        _docs_svc = build("docs", "v1", credentials=creds)
        return _docs_svc
    except Exception as e:
        log.error("Failed to init Docs service: %s", e)
        return None


def list_drive_files(company_name=None):
    svc = _drive()
    if not svc:
        return []
    try:
        parts = ["trashed = false"]
        if GOOGLE_DRIVE_FOLDER_ID:
            parts.append(f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents")
        if company_name:
            safe = company_name.replace("'", "\\'")
            parts.append(f"name contains '{safe}'")
        r = (
            svc.files()
            .list(
                q=" and ".join(parts),
                pageSize=50,
                fields="files(id,name,mimeType,modifiedTime,webViewLink)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        return r.get("files", [])
    except Exception as e:
        log.warning("Drive list_files failed: %s", e)
        return []


def _sanitize_doc_name(name):
    """Remove characters invalid in Drive file names: / \\ : * ? \" < > |"""
    if not name or not isinstance(name, str):
        return "Document"
    for c in '/\\:*?"<>|':
        name = name.replace(c, " ")
    return " ".join(name.split()).strip() or "Document"


def create_doc_in_drive(display_name, content):
    """
    Create a new Google Doc in the configured Drive folder (MAIN_FOLDER_ID) with the given content.
    display_name: used as the document title (e.g. "CompanyName - Brief" or "CompanyName - Research").
    content: plain text or HTML. If HTML, upload as HTML and let Drive convert to Doc (preserves tables/formatting).
    Returns (file_id, webViewLink, error_message). On success error_message is None.
    """
    if not content and content is not None:
        content = ""
    content = (content or "").strip()
    name = _sanitize_doc_name(display_name)
    if not name:
        name = "Document"
    # Save only to main folder (MAIN_FOLDER_ID)
    folder_id = (MAIN_FOLDER_ID or "").strip()
    if not folder_id:
        log.warning("MAIN_FOLDER_ID not set; cannot save doc to Drive")
        return None, None, "MAIN_FOLDER_ID is not set in keys.env. Add MAIN_FOLDER_ID=<your Drive folder ID>."
    svc = _drive()
    if not svc:
        log.warning("Drive service not available for create_doc_in_drive")
        _creds_path = _creds_file() or "credentials.json"
        _has_creds = os.path.exists(_creds_path) if _creds_path else False
        if _has_creds:
            return None, None, (
                "Google Drive is not configured. From the project folder run (use the same env as your app, e.g. activate your venv first):\n\n"
                "  python drive_auth.py\n\n"
                "A browser will open to sign in; then token.json is created and Save to Drive will work. "
                "Alternatively, set GOOGLE_SERVICE_ACCOUNT_JSON in keys.env and share MAIN_FOLDER_ID with that account."
            )
        return None, None, (
            "Google Drive is not configured. Either:\n\n"
            "1) OAuth: Put credentials.json in the project folder, then run:  python drive_auth.py  (browser will open to sign in).\n\n"
            "2) Service account: Set GOOGLE_SERVICE_ACCOUNT_JSON in keys.env and share MAIN_FOLDER_ID with that account."
        )
    try:
        from ui_helpers import is_html_content, html_to_plain_text
        is_html = is_html_content(content)
    except Exception:
        is_html = False
    try:
        if is_html and content:
            # Upload HTML and let Drive convert to Google Doc (preserves tables, headings, etc.)
            from googleapiclient.http import MediaIoBaseUpload
            if not content.strip().lower().startswith("<!doctype") and not content.strip().lower().startswith("<html"):
                content = "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>\n" + content + "\n</body></html>"
            file_metadata = {"name": name, "mimeType": "application/vnd.google-apps.document", "parents": [folder_id]}
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode("utf-8")),
                mimetype="text/html",
                resumable=False,
            )
            meta = svc.files().create(body=file_metadata, media_body=media, fields="id,webViewLink").execute()
        else:
            # Plain text: create empty Doc and insert text
            docs_svc = _docs()
            if not docs_svc:
                return None, None, "Google Docs service could not be initialized. Check your Drive credentials."
            body = {"name": name, "mimeType": "application/vnd.google-apps.document", "parents": [folder_id]}
            meta = svc.files().create(body=body, fields="id,webViewLink").execute()
            file_id = meta.get("id")
            if not file_id:
                return None, None, "Drive did not return a file ID."
            text_to_insert = (html_to_plain_text(content) if is_html else content) or ""
            if text_to_insert:
                docs_svc.documents().batchUpdate(
                    documentId=file_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": text_to_insert}}]},
                ).execute()
        file_id = meta.get("id")
        if not file_id:
            return None, None, "Drive did not return a file ID."
        log.info("Created Drive doc: %s (id=%s)", name, file_id)
        return file_id, meta.get("webViewLink"), None
    except Exception as e:
        err_msg = str(e).strip()
        log.error("create_doc_in_drive failed: %s", e)
        if "403" in err_msg or "Forbidden" in err_msg or "permission" in err_msg.lower():
            hint = " Share the folder with the account that owns the credentials (or, if using OAuth, delete token.json and sign in again to grant write access)."
            return None, None, f"Drive permission denied.{hint} Details: {err_msg}"
        if "404" in err_msg or "not found" in err_msg.lower():
            return None, None, f"Folder not found. Check that MAIN_FOLDER_ID ({folder_id}) is correct and the account has access. Details: {err_msg}"
        return None, None, err_msg or "Could not create document in Drive."


def fetch_doc_text(file_id, mime):
    cached = _file_cache.get(file_id)
    if cached and (datetime.utcnow() - cached["at"]).total_seconds() < 600:
        return cached["text"]

    text = None

    if mime == "application/vnd.google-apps.document":
        ds = _docs()
        if ds:
            try:
                doc = ds.documents().get(documentId=file_id).execute()
                parts = []
                for el in doc.get("body", {}).get("content", []):
                    if "paragraph" in el:
                        for e in el["paragraph"].get("elements", []):
                            if "textRun" in e:
                                parts.append(e["textRun"].get("content", ""))
                text = "".join(parts).strip()
            except Exception as e:
                log.warning("Failed to read Google Doc %s: %s", file_id, e)

    elif mime == "application/pdf":
        svc = _drive()
        if svc:
            try:
                from googleapiclient.http import MediaIoBaseDownload
                request = svc.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                fh.seek(0)

                from pypdf import PdfReader
                reader = PdfReader(fh)
                text = "\n".join(pg.extract_text() or "" for pg in reader.pages).strip()
            except ImportError:
                try:
                    from PyPDF2 import PdfReader
                    fh.seek(0)
                    reader = PdfReader(fh)
                    text = "\n".join(pg.extract_text() or "" for pg in reader.pages).strip()
                except Exception as e2:
                    log.warning("Failed to read PDF %s (no PDF library): %s", file_id, e2)
            except Exception as e:
                log.warning("Failed to read PDF %s: %s", file_id, e)

    if text:
        _file_cache[file_id] = {"text": text, "at": datetime.utcnow()}
    return text


_COMPANY_CONTEXT_CACHE_TTL_SEC = 12 * 3600
_COMPANY_CONTEXT_CACHE_KEY_PREFIX = "drive_ctx:company:"


def _normalize_cache_token(value: str) -> str:
    if not value:
        return ""
    v = value.strip().lower()
    v = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in v)
    return "-".join([p for p in v.split("-") if p])


def _drive_company_cache_key(company_name, company_domain=None):
    if company_domain and str(company_domain).strip():
        token = _normalize_cache_token(str(company_domain))
        return f"{_COMPANY_CONTEXT_CACHE_KEY_PREFIX}{token}"
    if company_name and str(company_name).strip():
        token = _normalize_cache_token(str(company_name))
        return f"{_COMPANY_CONTEXT_CACHE_KEY_PREFIX}{token}"
    return None


def _get_cached_company_context(cache_key: str):
    if not cache_key:
        return None
    mem = _company_context_cache.get(cache_key)
    if mem:
        age = (datetime.utcnow() - mem["at"]).total_seconds()
        if age <= _COMPANY_CONTEXT_CACHE_TTL_SEC:
            return mem.get("text", "")
    cached = get_cached_context(cache_key, max_age_sec=_COMPANY_CONTEXT_CACHE_TTL_SEC)
    if cached is not None:
        _company_context_cache[cache_key] = {"text": cached, "at": datetime.utcnow()}
    return cached


def _set_cached_company_context(cache_key: str, text: str):
    if not cache_key:
        return
    _company_context_cache[cache_key] = {"text": text or "", "at": datetime.utcnow()}
    save_cached_context(cache_key, text or "")


def refresh_all_drive_contexts():
    """Force refresh of global knowledge and clear company Drive caches."""
    cleared = delete_cached_contexts_with_prefix(_COMPANY_CONTEXT_CACHE_KEY_PREFIX)
    _company_context_cache.clear()
    text = get_lh2_knowledge_context(force_refresh=True)
    return {"cleared_company_caches": cleared, "lh2_chars": len(text or "")}


def build_drive_context(company_name, company_domain=None):
    """Build text context from Drive files for AI strategist."""
    cache_key = _drive_company_cache_key(company_name, company_domain)
    if cache_key:
        cached = _get_cached_company_context(cache_key)
        if cached is not None:
            return cached

    files = list_drive_files(company_name=company_name)
    if not files and company_domain:
        files = list_drive_files(company_name=company_domain)
    if not files:
        _set_cached_company_context(cache_key, "")
        return ""

    parts = []
    tokens = 0
    for f in files[:10]:
        text = fetch_doc_text(f["id"], f.get("mimeType", ""))
        if not text:
            continue
        ft = len(text) // 4
        if tokens + ft > MAX_DRIVE_TOKENS:
            rem = MAX_DRIVE_TOKENS - tokens
            if rem > 200:
                text = text[: rem * 4] + "\n...(truncated)"
                ft = rem
            else:
                break
        parts.append(f"--- {f['name']} ---\n{text}")
        tokens += ft

    out = "\n\n".join(parts)
    _set_cached_company_context(cache_key, out)
    return out


def sync_all_drive_pdfs(folder_id=None):
    """
    Sync all PDFs from Drive folder into a single context string.
    Same as notion_research_bot.py's sync_drive_memory().
    Used to feed the research agent.
    """
    svc = _drive()
    if not svc:
        return ""

    fid = folder_id or GOOGLE_DRIVE_FOLDER_ID
    if not fid:
        return ""

    try:
        from googleapiclient.http import MediaIoBaseDownload

        query = f"'{fid}' in parents and mimeType='application/pdf'"
        results = svc.files().list(q=query, fields="files(id, name)").execute()
        items = results.get("files", [])

        full_text = ""
        for item in items:
            request = svc.files().get_media(fileId=item["id"])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)

            try:
                from pypdf import PdfReader
                reader = PdfReader(fh)
            except ImportError:
                from PyPDF2 import PdfReader
                reader = PdfReader(fh)

            doc_text = ""
            for page in reader.pages:
                doc_text += (page.extract_text() or "") + "\n"

            full_text += f"\n\n--- {item['name']} ---\n{doc_text}"

        log.info("Drive sync: %d PDFs, %d chars", len(items), len(full_text))
        return full_text

    except Exception as e:
        log.error("Drive sync failed: %s", e)
        return ""


# ── Global LH2 / Accelerate / offerings context (all folders) ──

_LH2_KNOWLEDGE_CACHE = {}
_LH2_KNOWLEDGE_CACHE_AT = None
_LH2_CACHE_TTL_SEC = 12 * 3600  # 12 hours
_LH2_CACHE_KEY = "drive_ctx:lh2_knowledge"
MAX_LH2_KNOWLEDGE_CHARS = 100000  # cap for global context


def _list_all_files_recursive(folder_id, svc, max_files=80):
    """List all files under folder_id (recursive). Returns list of {id, name, mimeType}."""
    out = []
    stack = [folder_id]
    seen = set()

    while stack and len(out) < max_files:
        fid = stack.pop()
        if fid in seen:
            continue
        seen.add(fid)
        try:
            r = svc.files().list(
                q=f"'{fid}' in parents and trashed = false",
                pageSize=50,
                fields="files(id,name,mimeType)",
            ).execute()
            for f in r.get("files", []):
                mime = f.get("mimeType", "")
                if mime == "application/vnd.google-apps.folder":
                    stack.append(f["id"])
                elif "document" in mime or mime == "application/pdf":
                    out.append({"id": f["id"], "name": f.get("name", "?"), "mimeType": mime})
        except Exception as e:
            log.warning("List folder %s failed: %s", fid, e)
    return out


def get_lh2_knowledge_context(force_refresh=False):
    """
    Build a single context string from the linked Drive (all folders) describing
    LH2, Accelerate, and offerings. Used so the AI knows what we do when researching
    people and suggesting targets. Uses MAIN_FOLDER_ID when set, else GOOGLE_DRIVE_FOLDER_ID. Cached for 12 hours.
    """
    global _LH2_KNOWLEDGE_CACHE, _LH2_KNOWLEDGE_CACHE_AT
    now = datetime.utcnow()
    if not force_refresh and _LH2_KNOWLEDGE_CACHE_AT and (now - _LH2_KNOWLEDGE_CACHE_AT).total_seconds() < _LH2_CACHE_TTL_SEC:
        return _LH2_KNOWLEDGE_CACHE.get("text", "")

    if not force_refresh:
        cached = get_cached_context(_LH2_CACHE_KEY, max_age_sec=_LH2_CACHE_TTL_SEC)
        if cached is not None:
            _LH2_KNOWLEDGE_CACHE["text"] = cached
            _LH2_KNOWLEDGE_CACHE_AT = now
            return cached

    svc = _drive()
    knowledge_folder_id = MAIN_FOLDER_ID or GOOGLE_DRIVE_FOLDER_ID
    if not svc or not knowledge_folder_id:
        return ""

    try:
        files = _list_all_files_recursive(knowledge_folder_id, svc, max_files=80)
        parts = []
        total_chars = 0
        for f in files:
            if total_chars >= MAX_LH2_KNOWLEDGE_CHARS:
                break
            text = fetch_doc_text(f["id"], f.get("mimeType", ""))
            if not text or len(text) < 50:
                continue
            take = min(len(text), MAX_LH2_KNOWLEDGE_CHARS - total_chars - 200)
            if take < 100:
                continue
            block = f"--- {f['name']} ---\n{text[:take]}" + ("\n...(truncated)" if len(text) > take else "")
            parts.append(block)
            total_chars += len(block)
        out = "\n\n".join(parts) if parts else ""
        _LH2_KNOWLEDGE_CACHE["text"] = out
        _LH2_KNOWLEDGE_CACHE_AT = now
        save_cached_context(_LH2_CACHE_KEY, out)
        log.info("LH2 knowledge context: %d files, %d chars", len(parts), len(out))
        return out
    except Exception as e:
        log.error("get_lh2_knowledge_context failed: %s", e)
        return ""


# ── Context Folder Files (Training Docs, Outreach Guidelines, etc.) ──

_CONTEXT_CACHE = {}
_CONTEXT_CACHE_AT = None
_CONTEXT_CACHE_TTL_SEC = 12 * 3600  # 12 hours
_CONTEXT_CACHE_KEY = "drive_ctx:context_folder"


def get_context_folder_files(force_refresh=False):
    """
    Load ALL files from the Context folder (CONTEXT_FOLDER_ID).
    Returns a single text string with all files concatenated.
    Includes training docs, outreach guidelines, and any other context docs.
    Used for AI prompt caching - fetched once, cached in Anthropic prompt cache.
    """
    global _CONTEXT_CACHE, _CONTEXT_CACHE_AT
    now = datetime.utcnow()
    
    # Check in-memory cache
    if not force_refresh and _CONTEXT_CACHE_AT and (now - _CONTEXT_CACHE_AT).total_seconds() < _CONTEXT_CACHE_TTL_SEC:
        return _CONTEXT_CACHE.get("text", "")
    
    # Check database cache
    if not force_refresh:
        cached = get_cached_context(_CONTEXT_CACHE_KEY, max_age_sec=_CONTEXT_CACHE_TTL_SEC)
        if cached is not None:
            _CONTEXT_CACHE["text"] = cached
            _CONTEXT_CACHE_AT = now
            return cached
    
    # Load from Drive
    svc = _drive()
    if not svc or not CONTEXT_FOLDER_ID:
        log.warning("Context folder not configured (CONTEXT_FOLDER_ID missing)")
        return ""
    
    try:
        # List all files in Context folder (non-recursive)
        r = svc.files().list(
            q=f"'{CONTEXT_FOLDER_ID}' in parents and trashed = false",
            pageSize=100,
            fields="files(id,name,mimeType)",
        ).execute()
        files = r.get("files", [])
        
        if not files:
            log.info("No files found in Context folder")
            return ""
        
        # Fetch text from each file
        parts = []
        for f in files:
            mime = f.get("mimeType", "")
            if "document" not in mime and mime != "application/pdf":
                continue  # Skip non-doc/PDF files
            
            text = fetch_doc_text(f["id"], mime)
            if not text or len(text) < 50:
                continue
            
            parts.append(f"--- {f['name']} ---\n{text}")
        
        out = "\n\n".join(parts) if parts else ""
        
        # Cache the result
        _CONTEXT_CACHE["text"] = out
        _CONTEXT_CACHE_AT = now
        save_cached_context(_CONTEXT_CACHE_KEY, out)
        
        log.info("Context folder loaded: %d files, %d chars", len(parts), len(out))
        return out
    
    except Exception as e:
        log.error("get_context_folder_files failed: %s", e)
        return ""