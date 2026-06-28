"""
Backend-specific tests for django-zodb-backend.

These test ZODB-specific behaviors that aren't covered by Django's own
test suite — things like PK sequencing, BTree storage, in-memory test
database creation, and ZODB transaction semantics.
"""
