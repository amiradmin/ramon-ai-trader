from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,django").split(",") if h.strip()]
ROOT_URLCONF = "ramon_django.urls"
WSGI_APPLICATION = "ramon_django.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ea_bridge",
    "ramon_pipeline",
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
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
EA_BEARER_TOKEN = os.environ.get("RAMON_EA_BEARER_TOKEN", "")
EA_MAX_BODY_BYTES = int(os.environ.get("RAMON_EA_MAX_BODY_BYTES", "65536"))
RAMON_PIPELINE_INGEST_ENABLED = os.environ.get("RAMON_PIPELINE_INGEST_ENABLED", "0") == "1"
RAMON_DJANGO_DECISION_ENABLED = os.environ.get("RAMON_DJANGO_DECISION_ENABLED", "1") == "1"
RAMON_REDIS_URL = os.environ.get("RAMON_REDIS_URL", "redis://127.0.0.1:6379/0")
RAMON_REDIS_MARKET_MAXLEN = int(os.environ.get("RAMON_REDIS_MARKET_MAXLEN", "100000"))
RAMON_REDIS_CONSUMER = os.environ.get("RAMON_REDIS_CONSUMER", "ramon-worker-1")

DB_ENGINE = os.environ.get("DJANGO_DB_ENGINE", "sqlite").lower()
if DB_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": os.environ["DJANGO_DB_NAME"], "USER": os.environ["DJANGO_DB_USER"], "PASSWORD": os.environ["DJANGO_DB_PASSWORD"], "HOST": os.environ.get("DJANGO_DB_HOST", "timescaledb"), "PORT": os.environ.get("DJANGO_DB_PORT", "5432"), "CONN_MAX_AGE": 60, "OPTIONS": {"connect_timeout": 5}}}
else:
    DB_PATH = Path(os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "data" / "django.sqlite3")))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(DB_PATH)}}
