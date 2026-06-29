.. _getting-started:

===============
Getting started
===============

This page shows the smallest useful setup for trying ``django-zodb-backend`` in a
Django project.

Installation
============

Install the backend and its runtime dependencies:

.. code-block:: bash

   pip install django-zodb-backend

For local development of this repository:

.. code-block:: bash

   pip install -e .

.. note::

   The package requires Python 3.12+ and currently targets Django 6.0+ and ZODB 6.x.

Minimal database configuration
==============================

The backend is configured as a normal Django database engine.  The storage backend
is selected automatically from the settings you provide — no ``"storage"`` key needed.

For durable single-process deployments, set ``OPTIONS["PATH"]``:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "mydb",
           "OPTIONS": {
               "PATH": "var/mydb.fs",
           },
       }
   }

   DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

For tests and local experiments, leave both ``HOST`` and ``OPTIONS["PATH"]`` unset —
the backend uses in-process memory storage automatically.  See :doc:`testing` and
:doc:`storage-backends`.

Why ``BigAutoField`` matters
============================

Unlike MongoDB, ZODB fits naturally with Django's integer-primary-key defaults.
The backend stores each table in a ``BTrees.OOBTree.OOBTree``, which accepts any
Python object as a key. Integer PKs (``BigAutoField``) are the most common case
and map efficiently to ZODB's B-tree structures. That also means the backend does
**not** need the ObjectId-related test-suite patches that the MongoDB fork required —
a significant reduction in the amount of Django core changes needed.
See :doc:`comparison` and :doc:`django-fork` for the full rationale.

Your first model
================

A regular Django model works as expected:

.. code-block:: python

   from django.db import models


   class Article(models.Model):
       title = models.CharField(max_length=200)
       slug = models.SlugField(unique=True)
       body = models.TextField()
       published = models.BooleanField(default=False)

       def __str__(self):
           return self.title

From Django's perspective, this is still a standard ORM model. The difference is in
how persistence happens underneath:

* the model's table becomes a ZODB OOBTree in the root object,
* the row primary key is a 64-bit integer,
* the stored record is a persistent mapping rather than a SQL row.

A representative root layout is documented in :doc:`architecture`.

Running migrations
==================

Migrations work normally — ``SchemaEditor`` maps each migration operation to a
ZODB operation rather than SQL DDL:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Django migration operation
     - ZODB equivalent
   * - ``CreateModel``
     - ``root[table_name] = OOBTree()``  + sequence counter
   * - ``DeleteModel``
     - Remove BTree, sequence counter, index containers
   * - ``AddField`` / ``RemoveField`` / ``AlterField``
     - No-op (ZODB is schema-free; objects carry their own shape)
   * - ``AlterModelTable``
     - Rename the BTree key in the root
   * - ``AddIndex`` / ``RemoveIndex``
     - Create or drop sidecar index containers

.. code-block:: bash

   python manage.py makemigrations
   python manage.py migrate

.. tip::

   Adding a field to a model does not require rewriting existing objects. Older
   objects simply do not have the new attribute yet — code handles that with normal
   Python ``getattr(obj, "field", default)`` semantics.

In the test suite, ``DatabaseCreation.create_test_db()`` runs ``migrate --run-syncdb``
automatically against the in-memory store, so schema creation in tests mirrors
real-deployment behaviour exactly. No ``MIGRATION_MODULES`` suppression is needed.

Quick storage choices
=====================

``django-zodb-backend`` supports three ZODB storage layers, selected automatically
from your settings — no ``"storage"`` key needed:

``OPTIONS["PATH"]`` set, no ``HOST``
   ``FileStorage`` backed by a ``.fs`` append-only file.  The normal choice
   for single-node development and production.

``HOST`` set
   ``ClientStorage`` connected to a ZEO server for multi-process deployments.

Neither set
   In-process ``MappingStorage``.  Tests and local experimentation only —
   data does not persist across process restarts.

Why tests are convenient
========================

The in-memory ``MappingStorage`` test path is a major ergonomic win over backends that
require a separate database daemon. Django's test runs can start with a fresh in-memory
store and avoid provisioning an external service entirely. More detail is in
:doc:`testing`.
