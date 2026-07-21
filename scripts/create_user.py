#!/usr/bin/env python
"""Create or update a LidValid user account (admin/editor/viewer).

There's no in-app user-management UI yet, so this is the only way to create
a second account -- e.g. to hand out real accounts after replacing the demo
admin, or to create viewer/editor test accounts for RBAC verification.

Usage:
    .venv/Scripts/python.exe scripts/create_user.py --email a@b.com --password secret --role editor --name "Jane Doe"

If the email already exists, its password/role/name are updated instead of
creating a duplicate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app import models, security

VALID_ROLES = ("admin", "editor", "viewer")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", required=True, choices=VALID_ROLES)
    parser.add_argument("--name", default="", help="Display name (optional)")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        user = db.query(models.User).filter_by(email=args.email).first()
        if user:
            user.password_hash = security.hash_password(args.password)
            user.role = args.role
            if args.name:
                user.display_name = args.name
            db.commit()
            print(f"Updated existing user: {args.email} (role={args.role})")
        else:
            user = models.User(
                email=args.email,
                password_hash=security.hash_password(args.password),
                display_name=args.name or args.email.split("@")[0],
                role=args.role,
            )
            db.add(user)
            db.commit()
            print(f"Created user: {args.email} (role={args.role})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
