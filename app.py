import os

from dotenv import load_dotenv
from flask import Flask

from core.external_refresh import configure_external_refresh
from core.flask_setup import (
    configure_app,
    register_blueprints,
    register_error_handlers,
    register_request_hooks,
    register_template_context,
)
from core.knowledge_runtime import refresh_knowledge_state
from core.runtime import initialize_runtime, log_embedding_status, seed_admin

load_dotenv()

runtime = initialize_runtime()
app = Flask(__name__)
configure_app(app)

ensure_external_refresh_started = configure_external_refresh(app, runtime)
register_request_hooks(app, ensure_external_refresh_started)
register_blueprints(app)
register_error_handlers(app)
register_template_context(app, runtime)

log_embedding_status(runtime, app.logger)
seed_admin(runtime.store, app.logger)

if __name__ == "__main__":
    ensure_external_refresh_started()
    refresh_knowledge_state(
        force_reindex=False,
        rebuild_index=os.getenv("RAG_REINDEX_ON_START", "0") == "1",
    )
    app.run(
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
    )
