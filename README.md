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

This backend adapts Django's ORM to ZODB by:

- Storing each model's instances in a `BTrees.LOBTree.LOBTree` (pk → object)
- Maintaining secondary BTree indexes for filtered lookups
- Translating Django `Q()` filters into BTree range scans and set intersections
- Wrapping ZODB transactions in Django's `atomic()` protocol

## Quick Start

```python
# settings.py
DATABASES = {
    "default": {
        "ENGINE": "django_zodb_backend",
        "NAME": "mydb",             # used as root key namespace
        "OPTIONS": {
            "storage": "memory",    # or "file", "zeo", "relstorage"
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

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
