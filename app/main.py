from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .database import init_db, SessionLocal
from .routers import ui, api
from .auth import RedirectToLogin, redirect_to_login_handler
from .services import run_service
from . import security, models

BASE_DIR = Path(__file__).resolve().parent


def _bootstrap_admin() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            email = os.environ.get("LIDVALID_ADMIN_EMAIL")
            password = os.environ.get("LIDVALID_ADMIN_PASSWORD")
            if not email or not password:
                if os.environ.get("LIDVALID_ENV", "development") == "production":
                    raise RuntimeError(
                        "LIDVALID_ADMIN_EMAIL and LIDVALID_ADMIN_PASSWORD must be set "
                        "for first-run bootstrap when LIDVALID_ENV=production."
                    )
                email = email or "admin@lidvalid.local"
                password = password or "admin123"
                print("WARNING: bootstrapping with DEV-ONLY demo admin credentials.")
            admin = models.User(
                email=email,
                password_hash=security.hash_password(password),
                display_name="Admin",
                role="admin",
            )
            db.add(admin)
            db.commit()
            print("=" * 60)
            print("LidValid — first run: created bootstrap admin user")
            print(f"  email:    {email}")
            if email == "admin@lidvalid.local":
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
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_exception_handler(RedirectToLogin, redirect_to_login_handler)

app.include_router(ui.router)
app.include_router(api.router, prefix="/api")
