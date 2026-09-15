"""Demo settings for the django-trusts example. Not for production."""

import os
from pathlib import Path

import dj_database_url

# Django's MySQL backend imports MySQLdb; PyMySQL provides that API.
# Only needed when DATABASE_URL points at MySQL (dokku-mysql).
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if _DATABASE_URL.startswith(("mysql://", "mysql2://")):
    import pymysql

    pymysql.install_as_MySQLdb()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-trusts-example-demo-not-for-production",
)
DEBUG = True
ALLOWED_HOSTS = ["*"]

# Dokku/nginx terminates TLS and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HTTPS login POST needs this (Django 4+). Comma-separated via env; empty
# locally. Dokku HTTPS must set CSRF_TRUSTED_ORIGINS (see README).
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# Match Trusts: keep AutoField so example models do not switch to BigAutoField.
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "trusts.zero.apps.ZeroConfig",
    "projects.apps.ProjectsConfig",
]

AUTHENTICATION_BACKENDS = [
    "trusts.zero.backends.TrustModelBackend",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "example.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "example" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "example.wsgi.application"

# DATABASE_URL (dokku-mysql) when set; otherwise local SQLite.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# Permission filtering must run before this limit is applied.
PROJECT_PAGE_SIZE = 3

# Demo-only: seed_demo creates these accounts with password "demo".
DEMO_PASSWORD = "demo"
