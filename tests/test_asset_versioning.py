"""Regression tests for TWO stacked caching incidents around app.css.

Incident 1: after deploying a CSS rewrite (the copy-key modal popup), a
user's browser kept serving the OLD cached app.css -- StaticFiles sets no
Cache-Control override, so the browser was free to cache it heuristically --
and the new modal markup rendered as plain unstyled divs stuck in normal
page flow instead of a centered popup, even though the server was confirmed
(via curl) to be serving the updated file. Fix: every template extends
base.html, which links the stylesheet with a `?v=<app.css mtime>` query
string, so any future CSS change naturally produces a NEW url and a stale
cached copy under the OLD url is simply never requested again.

Incident 2: the first fix computed that mtime ONCE, at module import time,
so the version string only changed on a server restart. Editing app.css
again without restarting (routine during active development) silently
reproduced the exact same stale-cache bug one layer up -- the URL never
changed even though the file did. Fix: `asset_version` is a callable that
stats the file fresh on every render, removing the restart-timing
dependency entirely.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

import app.main as main_module


def test_every_page_links_a_version_stamped_stylesheet():
    client = TestClient(main_module.app)
    r = client.get("/login")
    assert r.status_code == 200
    m = re.search(r'href="/static/app\.css\?v=(\d+)"', r.text)
    assert m, f"stylesheet link missing a cache-busting version query string: {r.text[:500]}"
    assert int(m.group(1)) > 0


def test_asset_version_matches_the_actual_css_file_mtime():
    import os
    from pathlib import Path

    from app.routers.ui import templates

    css_path = Path(__file__).resolve().parent.parent / "app" / "static" / "app.css"
    assert templates.env.globals["asset_version"]() == int(os.stat(css_path).st_mtime)


def test_asset_version_reflects_a_file_change_without_reimporting_the_module():
    """The whole point of making it a callable: editing app.css AFTER the
    process has already started (no restart) must still produce a new
    version on the very next render -- this is exactly the incident that
    a frozen-at-import value silently reintroduced."""
    import os
    from pathlib import Path

    from app.routers.ui import templates

    css_path = Path(__file__).resolve().parent.parent / "app" / "static" / "app.css"
    before = templates.env.globals["asset_version"]()

    original = css_path.read_bytes()
    try:
        new_mtime = int(os.stat(css_path).st_mtime) + 5
        css_path.write_bytes(original)  # touch content
        os.utime(css_path, (new_mtime, new_mtime))
        after = templates.env.globals["asset_version"]()
        assert after != before
        assert after == new_mtime
    finally:
        css_path.write_bytes(original)
