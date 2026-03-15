"""
Single entry point for SQLite connection and paths.

One SQLite DB (lh2_platform.db) is used for both main app and advisor data.
All SQLite files live in this folder (or ~/.lh2_platform/sqlite-db as fallback).
No other module should reference SQLite paths or create SQLite engines directly.

Usage from project root:
  from sqlite_db_connection import get_local_db_directory, create_platform_engine
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

log = logging.getLogger(__name__)

# This file lives in sqlite-db/, so this directory is the primary SQLite data dir
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FALLBACK_DIR = os.path.join(os.path.expanduser("~"), ".lh2_platform", "sqlite-db")
# Legacy: project root had lh2_platform.db before we moved to sqlite-db/
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_LEGACY_PLATFORM_PATH = os.path.join(_PROJECT_ROOT, "lh2_platform.db")

PLATFORM_DB_NAME = "lh2_platform.db"


def _path_writable(db_dir: str, db_path: str) -> bool:
    if not os.access(db_dir, os.W_OK):
        return False
    if os.path.exists(db_path) and not os.access(db_path, os.W_OK):
        return False
    return True


def _migrate_legacy_platform():
    """Copy root lh2_platform.db into this folder once if it exists and we don't have it yet."""
    primary_path = os.path.join(_THIS_DIR, PLATFORM_DB_NAME)
    if os.path.exists(primary_path):
        return
    if not os.path.exists(_LEGACY_PLATFORM_PATH) or os.path.getsize(_LEGACY_PLATFORM_PATH) == 0:
        return
    try:
        shutil.copy2(_LEGACY_PLATFORM_PATH, primary_path)
        log.info("Migrated legacy SQLite to sqlite-db: %s", primary_path)
    except Exception as e:
        log.warning("Could not migrate legacy SQLite: %s", e)


def get_local_db_directory() -> str:
    """
    Return the writable directory for all SQLite DBs (this folder or fallback).
    Used by main app and advisor; both use lh2_platform.db in this folder.
    """
    try:
        os.makedirs(_THIS_DIR, mode=0o755, exist_ok=True)
        _migrate_legacy_platform()
        platform_path = os.path.join(_THIS_DIR, PLATFORM_DB_NAME)
        if _path_writable(_THIS_DIR, platform_path):
            return _THIS_DIR
    except OSError:
        pass
    try:
        os.makedirs(_FALLBACK_DIR, mode=0o700, exist_ok=True)
        fallback_platform = os.path.join(_FALLBACK_DIR, PLATFORM_DB_NAME)
        if _path_writable(_FALLBACK_DIR, fallback_platform):
            log.info("Project sqlite-db not writable; using fallback: %s", _FALLBACK_DIR)
            return _FALLBACK_DIR
    except OSError as e:
        log.warning("Could not use fallback SQLite dir %s: %s", _FALLBACK_DIR, e)
    raise RuntimeError(
        f"SQLite directory is not writable: {_THIS_DIR!r}. "
        "Fix permissions or use PostgreSQL (set DATABASE_URL in keys.env)."
    )


def get_platform_db_path() -> str:
    """Full path to lh2_platform.db in the active SQLite directory."""
    return os.path.join(get_local_db_directory(), PLATFORM_DB_NAME)


def create_platform_engine():
    """Create SQLAlchemy engine for lh2_platform.db. NullPool for multi-process safety."""
    path = get_platform_db_path()
    log.info("Using SQLite at %s", path)
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )


