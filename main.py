import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database.init_db import init_db, startup_db_init
from services.auth_service import is_authenticated

from routers.auth import router as auth_router
from routers.dashboard import router as dashboard_router
from routers.settings import router as settings_router
from routers.health import router as health_router
from routers.reports import router as reports_router
from routers.webhooks import router as webhooks_router

app = FastAPI(
    title="Offline Attribution Engine (Multi-Tenant Multi-Channel)",
    description="Multi-tenant agency platform for tracking offline leads/sales and AI audits across Google, Meta, LinkedIn, and Microsoft",
    version="15.2.0"
)

@app.on_event("startup")
def on_startup():
    startup_db_init()

@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    email = is_authenticated(request)
    if email:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(health_router)
app.include_router(reports_router)
app.include_router(webhooks_router)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f" Starting Uvicorn server on 0.0.0.0:{port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
