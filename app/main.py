from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .database import init_db, SessionLocal
from .routers import api
from .services import run_service
from . import security, models

BASE_DIR = Path(__file__).resolve().parent
# The React SPA's production build (see frontend/README or the root
# README's Deployment section) -- built by the Dockerfile's Node stage,
# or locally via `cd frontend && npm run build` for a non-Docker run.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


def _bootstrap_admin() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            username = os.environ.get("LIDVALID_ADMIN_USERNAME")
            password = os.environ.get("LIDVALID_ADMIN_PASSWORD")
            if not username or not password:
                if os.environ.get("LIDVALID_ENV", "development") == "production":
                    raise RuntimeError(
                        "LIDVALID_ADMIN_USERNAME and LIDVALID_ADMIN_PASSWORD must be set "
                        "for first-run bootstrap when LIDVALID_ENV=production."
                    )
                username = username or "admin"
                password = password or "admin123"
                print("WARNING: bootstrapping with DEV-ONLY demo admin credentials.")
            admin = models.User(
                username=username,
                password_hash=security.hash_password(password),
                display_name="Admin",
                role="admin",
            )
            db.add(admin)
            db.commit()
            print("=" * 60)
            print("LidValid — first run: created bootstrap admin user")
            print(f"  username: {username}")
            if username == "admin":
                print("  password: admin123  (change this in Settings)")
            print("=" * 60)

        # Any run still "running"/"queued" belonged to a PREVIOUS process --
        # its background thread died with that process and will never
        # update it again. Reap it now so it doesn't sit "running" forever
        # looking alive (see run_service.reap_orphaned_runs docstring for
        # the incident that prompted this).
        reaped = run_service.reap_orphaned_runs(db)
        if reaped:
            print(f"LidValid — {reaped} run dari proses sebelumnya ditandai 'failed' (server sempat mati/di-restart saat run itu jalan).")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_admin()
    yield


app = FastAPI(
    title="LidValid",
    description="Validasi data, tuntas sampai baris terakhir.",
    lifespan=lifespan,
)
app.add_middleware(SessionMiddleware, secret_key=security._load_or_create_key().decode("ascii"))
app.include_router(api.router, prefix="/api")

# Vite's hashed bundle filenames (index-<hash>.js/css) live under dist/assets
# and are safe to cache forever; mounted directly if the frontend has been
# built (skipped otherwise so importing this module -- e.g. under pytest --
# doesn't require a Node build to exist).
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="spa-assets")


@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_not_found(full_path: str):
    """A real 404 for any /api/* path no route above matched -- registered
    before the SPA catch-all below so an unmatched API path doesn't
    silently fall through to it and return index.html with a 200."""
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Client-side routing fallback: any path that isn't `/api/*` or a real
    built file (favicon.svg, etc.) gets index.html, and React Router takes
    it from there. Must be the LAST route registered -- `/api/*` and
    `/assets/*` above need to win first, since Starlette matches path
    templates in registration order and this `{full_path:path}` would
    otherwise swallow everything."""
    if not FRONTEND_DIST.is_dir():
        return PlainTextResponse(
            "Frontend build not found. Run `cd frontend && npm run build` "
            "(or `npm run dev` there for local development against this API).",
            status_code=503,
        )
    requested = FRONTEND_DIST / full_path
    if full_path and requested.is_file():
        return FileResponse(requested)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return PlainTextResponse("Frontend build not found.", status_code=503)
