"""Service registry — populated by app.py at startup.

Every module that needs access to shared services (store, rag, etc.)
imports from here instead of from app, breaking circular dependencies.
"""

store = None
auth_service = None
rag = None
tide_service = None
weather_service = None
ais_service = None
wave_service = None
local_warning_service = None
whatsapp_service = None
index_store = None

# Module-level mutable state
startup_migration_status = None
reindex_thread = None
reindex_thread_lock = None
reindex_retry_scheduler = None
wave_refresh_scheduler = None
local_warning_refresh_scheduler = None

# Constants (populated at startup)
BERTH_OPTIONS = []
TERMINAL_OPTIONS = []
VESSEL_TYPE_OPTIONS = []
CONSTRAINT_OPTIONS = []
BASE_DIR = ""
DATA_DIR = ""
KNOWLEDGE_DIR = ""
