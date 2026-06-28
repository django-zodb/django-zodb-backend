# Settings for running Django's own test suite against the ZODB backend.
#
# Mirrors the pattern from django-mongodb-backend's django_settings.py.
# Key differences from the MongoDB settings:
#
# - ENGINE is "django_zodb_backend" (not "django_mongodb_backend")
# - No HOST/PORT needed for in-memory storage (default for tests)
# - DEFAULT_AUTO_FIELD stays as BigAutoField — ZODB uses 64-bit integer PKs,
#   so we do NOT need the ObjectId-related test suite changes that the MongoDB
#   fork required. This significantly reduces the number of test adaptations.
# - USE_TZ = False to keep timezone handling simple in the POC.
# - No MIGRATION_MODULES override — migrations run normally via our
#   SchemaEditor which maps create_model/delete_model to OOBTree operations.

DATABASES = {
    "default": {
        "ENGINE": "django_zodb_backend",
        "NAME": "djangotests",
        "OPTIONS": {"storage": "memory"},
    },
    "other": {
        "ENGINE": "django_zodb_backend",
        "NAME": "djangotests-other",
        "OPTIONS": {"storage": "memory"},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PASSWORD_HASHERS = ("django.contrib.auth.hashers.MD5PasswordHasher",)
SECRET_KEY = "django_tests_secret_key"  # noqa: S105
USE_TZ = False
