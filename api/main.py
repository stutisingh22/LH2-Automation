from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.settings import CORS_ORIGINS
from api.routers import (
    advisor,
    advisor_chat_routes,
    auth,
    bulk_routes,
    calendar_routes,
    chat,
    companies,
    dashboard,
    history_routes,
    intelligence_routes,
    meta,
    notion_routes,
    plans,
    pipeline_routes,
    session_actions,
    sessions,
    spoc_routes,
    strategist_contacts,
    timeline_routes,
    users_routes,
    workflows_routes,
)

app = FastAPI(title="LH2 Command Center API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(meta.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(notion_routes.router, prefix="/api")
app.include_router(users_routes.router, prefix="/api")
app.include_router(companies.router, prefix="/api")
app.include_router(advisor.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(strategist_contacts.router, prefix="/api")
app.include_router(session_actions.router, prefix="/api")
app.include_router(plans.router, prefix="/api")
app.include_router(timeline_routes.router, prefix="/api")
app.include_router(calendar_routes.router, prefix="/api")
app.include_router(pipeline_routes.router, prefix="/api")
app.include_router(spoc_routes.router, prefix="/api")
app.include_router(history_routes.router, prefix="/api")
app.include_router(intelligence_routes.router, prefix="/api")
app.include_router(advisor_chat_routes.router, prefix="/api")
app.include_router(bulk_routes.router, prefix="/api")
app.include_router(workflows_routes.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
