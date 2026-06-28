"""
DB-API 2.0 stub for ZODB.

ZODB does not use a cursor-based DB-API. This module exists so that Django's
DatabaseWrapper machinery has a ``Database`` attribute to import, matching the
pattern established by django-mongodb-backend.
"""

# These exception classes satisfy Django's db error hierarchy expectations.
from django.db import (  # noqa: F401
    DatabaseError,
    DataError,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)

# Expose a minimal DB-API 2.0 interface so that Django's error wrapping works.
apilevel = "2.0"
threadsafety = 1
paramstyle = "format"

# DB-API 2.0 requires a Binary constructor for binary data.
# ZODB stores Python objects natively, so bytes suffices.
Binary = bytes
