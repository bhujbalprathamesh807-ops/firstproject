from pathlib import Path
import os
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs

# =========================================================
# BASE DIRECTORY
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# =========================================================
# SECURITY
# =========================================================

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-smart-expense-tracker-local-2026"
)

DEBUG = os.getenv(
    "DEBUG",
    "False"
).lower() in {"1", "true", "yes", "on"}


# =========================================================
# ALLOWED HOSTS
# =========================================================

_default_hosts = "127.0.0.1,localhost"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "ALLOWED_HOSTS",
        _default_hosts
    ).split(",")
    if host.strip()
]

# Vercel automatic hostname
if os.getenv("VERCEL_URL"):
    vercel_url = os.getenv("VERCEL_URL").split(":")[0]
    ALLOWED_HOSTS.append(vercel_url)

# Allow Vercel domains
ALLOWED_HOSTS.extend([
    ".vercel.app",
    ".now.sh",
])

ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS))


# =========================================================
# CSRF TRUSTED ORIGINS
# =========================================================

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        ""
    ).split(",")
    if origin.strip()
]

if os.getenv("VERCEL_URL"):
    CSRF_TRUSTED_ORIGINS.append(
        f"https://{os.getenv('VERCEL_URL')}"
    )

# Generic Vercel HTTPS origin
CSRF_TRUSTED_ORIGINS.append(
    "https://*.vercel.app"
)

CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(CSRF_TRUSTED_ORIGINS)
)


# =========================================================
# APPLICATIONS
# =========================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "expenses",
]


# =========================================================
# WHITENOISE
# =========================================================

try:
    from whitenoise.middleware import WhiteNoiseMiddleware
except ImportError:
    WhiteNoiseMiddleware = None


# =========================================================
# MIDDLEWARE
# =========================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if WhiteNoiseMiddleware is not None:
    MIDDLEWARE.insert(
        1,
        "whitenoise.middleware.WhiteNoiseMiddleware"
    )


# =========================================================
# URL / WSGI
# =========================================================

ROOT_URLCONF = "smart_expense_tracker.urls"

WSGI_APPLICATION = "smart_expense_tracker.wsgi.application"


# =========================================================
# TEMPLATES
# =========================================================

TEMPLATES = [
    {
        "BACKEND":
            "django.template.backends.django.DjangoTemplates",

        "DIRS": [
            BASE_DIR / "templates"
        ],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# =========================================================
# DATABASE
# =========================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()


if DATABASE_URL:

    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace(
            "postgres://",
            "postgresql://",
            1
        )

    db_url = urlparse(DATABASE_URL)

    query_params = parse_qs(
        db_url.query
    )

    DATABASES = {
        "default": {
            "ENGINE":
                "django.db.backends.postgresql",

            "NAME":
                db_url.path.lstrip("/"),

            "USER":
                db_url.username or "",

            "PASSWORD":
                db_url.password or "",

            "HOST":
                db_url.hostname or "",

            "PORT":
                str(db_url.port or 5432),

            "CONN_MAX_AGE":
                600,

            "OPTIONS": {
                "sslmode":
                    query_params.get(
                        "sslmode",
                        ["require"]
                    )[0]
            },
        }
    }

else:

    DATABASES = {
        "default": {
            "ENGINE":
                "django.db.backends.sqlite3",

            "NAME":
                BASE_DIR / "db.sqlite3",
        }
    }


# =========================================================
# PASSWORD VALIDATION
# =========================================================

AUTH_PASSWORD_VALIDATORS = []


# =========================================================
# INTERNATIONALIZATION
# =========================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Kolkata"

USE_I18N = True

USE_TZ = True


# =========================================================
# STATIC FILES
# =========================================================

STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static"
]

STATIC_ROOT = BASE_DIR / "staticfiles"


# =========================================================
# MEDIA FILES
# =========================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"


# =========================================================
# FILE STORAGE
# =========================================================

STORAGES = {
    "default": {
        "BACKEND":
            "django.core.files.storage.FileSystemStorage"
    }
}


if WhiteNoiseMiddleware is not None:

    STORAGES["staticfiles"] = {
        "BACKEND":
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
    }

else:

    STORAGES["staticfiles"] = {
        "BACKEND":
            "django.contrib.staticfiles.storage.StaticFilesStorage"
    }


# =========================================================
# DEFAULT PRIMARY KEY
# =========================================================

DEFAULT_AUTO_FIELD = (
    "django.db.models.BigAutoField"
)


# =========================================================
# LOGIN / LOGOUT
# =========================================================

LOGIN_URL = "login"

LOGIN_REDIRECT_URL = "dashboard"

LOGOUT_REDIRECT_URL = "home"


# =========================================================
# RAZORPAY
# =========================================================

RAZORPAY_KEY_ID = os.getenv(
    "RAZORPAY_KEY_ID",
    ""
)

RAZORPAY_KEY_SECRET = os.getenv(
    "RAZORPAY_KEY_SECRET",
    ""
)


# =========================================================
# PRODUCTION SECURITY
# =========================================================

if not DEBUG:

    SESSION_COOKIE_SECURE = True

    CSRF_COOKIE_SECURE = True

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https"
    )