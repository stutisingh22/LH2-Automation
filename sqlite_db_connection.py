"""
Loader for the SQLite connection wrapper. The only file in the project root that
references the sqlite-db/ folder. All other code should use:

  from sqlite_db_connection import (
      get_local_db_directory,
      get_platform_db_path,
      create_platform_engine,
  )
"""
import importlib.util
import os

_conn_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sqlite-db", "connection.py")
_spec = importlib.util.spec_from_file_location("_sqlite_connection", _conn_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

get_local_db_directory = _mod.get_local_db_directory
get_platform_db_path = _mod.get_platform_db_path
create_platform_engine = _mod.create_platform_engine
