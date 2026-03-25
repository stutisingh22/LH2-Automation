from fastapi import FastAPI

# Re-export the existing FastAPI application that mounts all `/api/*` routers.
from api.main import app as _api_app

app: FastAPI = _api_app


@app.get('/health')
def health() -> dict:
    return {"status": "ok"}
