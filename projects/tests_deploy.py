"""Deploy-glue checks: local SQLite default and Dokku MySQL DATABASE_URL."""

import os
import subprocess
import sys
from pathlib import Path

import dj_database_url
from django.conf import settings
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent


class DeploySettingsTests(SimpleTestCase):
    def test_local_default_is_sqlite(self):
        # The test runner may rewrite NAME to an in-memory DB; the engine
        # is what shows the demo still uses SQLite when DATABASE_URL is unset.
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.sqlite3",
        )

    def test_sqlite_fallback_url_points_at_db_file(self):
        parsed = dj_database_url.parse("sqlite:////tmp/demo/db.sqlite3")
        self.assertTrue(str(parsed["NAME"]).endswith("db.sqlite3"))

    def test_mysql_database_url_parses_for_dokku(self):
        parsed = dj_database_url.parse(
            "mysql://demo:s3cret@mysql.internal:3306/example"
        )
        self.assertEqual(parsed["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(parsed["NAME"], "example")
        self.assertEqual(parsed["USER"], "demo")
        self.assertEqual(parsed["HOST"], "mysql.internal")
        self.assertEqual(int(parsed["PORT"]), 3306)

    def test_csrf_trusted_origins_default_empty(self):
        self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, [])

    def test_csrf_trusted_origins_parses_env(self):
        env = os.environ.copy()
        env["CSRF_TRUSTED_ORIGINS"] = (
            "https://your-app.example.com, https://*.example.com"
        )
        env["DJANGO_SETTINGS_MODULE"] = "example.settings"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        )
        code = """
import django
from django.conf import settings

django.setup()
print("|".join(settings.CSRF_TRUSTED_ORIGINS))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stdout.strip(),
            "https://your-app.example.com|https://*.example.com",
        )

    def test_whitenoise_is_enabled(self):
        self.assertIn(
            "whitenoise.middleware.WhiteNoiseMiddleware",
            settings.MIDDLEWARE,
        )
        security_at = settings.MIDDLEWARE.index(
            "django.middleware.security.SecurityMiddleware"
        )
        whitenoise_at = settings.MIDDLEWARE.index(
            "whitenoise.middleware.WhiteNoiseMiddleware"
        )
        self.assertEqual(whitenoise_at, security_at + 1)

    def test_mysql_backend_initializes_with_installed_pymysql(self):
        """Django 6.1 loads the MySQL backend only if the driver reports 2.2.1+.

        Uses a dummy mysql:// URL in a subprocess so this does not rewrite
        the SQLite test database. Does not spoof PyMySQL's version_info.
        """
        env = os.environ.copy()
        env["DATABASE_URL"] = "mysql://demo:s3cret@127.0.0.1:3306/unused"
        env["DJANGO_SETTINGS_MODULE"] = "example.settings"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        )
        code = """
import django
from django.db import connections

django.setup()
conn = connections["default"]
if conn.vendor != "mysql":
    raise SystemExit(f"expected mysql vendor, got {conn.vendor}")
from django.db.backends.mysql.base import Database

if Database.version_info < (2, 2, 1):
    raise SystemExit(
        f"driver version_info {Database.version_info} is below Django 6.1"
    )
print("mysql-backend-ok", Database.version_info, Database.__version__)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertIn("mysql-backend-ok", result.stdout)

    def test_mysql_default_keeps_policy_alias_on_sqlite(self):
        """Dokku/CI may point default at MySQL. E009 stays on `policy`."""
        env = os.environ.copy()
        env["DATABASE_URL"] = "mysql://demo:s3cret@127.0.0.1:3306/unused"
        env["TRUSTS_POLICY_DATABASE"] = "policy"
        env["DJANGO_SETTINGS_MODULE"] = "example.settings"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        )
        code = """
import django
from django.conf import settings
from django.core import checks
from trusts.policy_lock import render_policy_sql_bytes, resolve_lockfile_path

django.setup()
alias = settings.TRUSTS_POLICY_DATABASE
if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.mysql":
    raise SystemExit("default engine is not mysql")
if alias != "policy":
    raise SystemExit(f"policy alias is {alias!r}")
if settings.DATABASES[alias]["ENGINE"] != "django.db.backends.sqlite3":
    raise SystemExit("policy engine is not sqlite")
# Limit model checks to the pinned alias. Plain check also visits
# default, which needs a live MySQL server; CI has one, this test does not.
messages = checks.run_checks(databases=[alias])
errors = [m for m in messages if m.level >= checks.ERROR]
if errors:
    raise SystemExit("\\n".join(f"{m.id}: {m.msg}" for m in errors))
committed = resolve_lockfile_path().path.read_bytes()
rendered = render_policy_sql_bytes()
if rendered != committed:
    raise SystemExit("policy lock bytes differ from the pinned alias")
print("policy-pin-ok", alias, settings.DATABASES[alias]["ENGINE"])
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertIn("policy-pin-ok policy django.db.backends.sqlite3", result.stdout)
