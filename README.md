# django-zodb-backend

A Django database backend for [ZODB](https://zodb.org/), the Zope Object Database.

> **Status: Proof of Concept / Pre-Alpha**
> This backend is under active development. The goal is to pass the full
> Django test suite, modelled after the approach taken by
> [django-mongodb-backend](https://github.com/mongodb/django-mongodb-backend).

## What is ZODB?

ZODB is a mature, ACID-compliant, transactional Python object database. It
stores Python objects directly — no schema, no SQL, no ORM translation layer.
Objects are addressed by OID and navigated from a root via Python attribute
traversal and sorted BTree containers.

## Architecture Overview

This backend is modelled after `django-mongodb-backend` but makes one critical
trade-off in the opposite direction: instead of translating Django's SQL abstract
syntax tree into another query language, it keeps Django's query construction
completely intact and performs execution directly against ZODB data structures.

In short: Django builds the query; the backend runs it.

- Each model's instances are stored in a `BTrees.OOBTree.OOBTree` (pk → object)
- `WHERE` clauses are evaluated as Python predicates against each stored object
- FK and M2M JOINs are resolved by walking Django's `alias_map` in Python
- Ordering, slicing, and aggregates are computed in Python
- ZODB transactions are wrapped in Django's `atomic()` protocol

This approach is the right fit for ZODB because ZODB has no query engine of its own.
The Python scan *is* the execution model.

## Quick Start

```python
# settings.py
DATABASES = {
    "default": {
        "ENGINE": "django_zodb_backend",
        "NAME": "mydb",
        "OPTIONS": {
            "path": "var/mydb.fs",   # → FileStorage
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

Storage is selected automatically:
- `OPTIONS["path"]` set → `FileStorage` (single-process, durable)
- `HOST` set → ZEO `ClientStorage` (multi-process)
- nothing set → `MappingStorage` (in-memory, tests/CI only)

## Documentation

Full documentation is in [docs/](docs/) and will be published at
https://django-zodb-backend.readthedocs.io.

## Development

```bash
pip install -e ".[dev]"
# Run Django's test suite against ZODB:
python runtests.py basic
```

## Comparison with django-mongodb-backend

See [docs/comparison.rst](docs/comparison.rst) for a detailed analysis of
architectural decisions made relative to the MongoDB backend.

## License

BSD 3-Clause License. See [LICENSE](LICENSE).
