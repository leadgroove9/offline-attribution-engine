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
    title="LeadGroove Offline Attribution Engine (Multi-Tenant)",
    description="Multi-tenant agency platform for tracking offline leads/sales and AI audits",
    version="15.3.0"
)

# Direct health check endpoints for cloud deployment probes
@app.get("/")
@app.get("/health")
@app.get("/healthz")
def root_health_check():
    return {"status": "ok", "service": "LeadGroove Offline Attribution Engine"}

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(health_router)
app.include_router(reports_router)
app.include_router(webhooks_router)

@app.on_event("startup")
def on_startup():
    startup_db_init()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"🌐 Starting LeadGroove Server on 0.0.0.0:{port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
