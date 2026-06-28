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

The backend is configured as a normal Django database engine:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "mydb",
           "OPTIONS": {
               "storage": "memory",
           },
       }
   }

   DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

Why ``BigAutoField`` matters
============================

Unlike MongoDB, ZODB fits naturally with Django's integer-primary-key defaults.
The backend stores each table in a ``BTrees.LOBTree.LOBTree``, whose keys are
64-bit integers. That makes ``BigAutoField`` the right primary-key type for the
project and dramatically reduces the amount of Django test-suite patching required.
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

Migrations still matter, but schema operations are much lighter than on an SQL backend.
``create_model()`` creates a BTree. ``delete_model()`` removes it. ``add_field()``
and ``remove_field()`` are effectively no-ops because stored Python objects carry their
own shape.

.. code-block:: bash

   python manage.py makemigrations
   python manage.py migrate

.. tip::

   Adding a field to a model does not require rewriting existing objects. Older
   objects simply do not have the new attribute yet, and code can handle that with
   ``getattr(obj, "field", default)`` semantics.

Quick storage choices
=====================

``django-zodb-backend`` is designed around several ZODB storage layers:

``memory``
   In-process ``MappingStorage``. Excellent for tests and local experiments.

``file``
   ``FileStorage`` backed by a ``.fs`` append-only file.

``zeo``
   ``ClientStorage`` connected to a ZEO server for multi-process deployments.

``relstorage``
   A planned/targeted configuration for storing ZODB pickles inside PostgreSQL,
   MySQL, or SQLite via RelStorage. See :doc:`storage-backends`.

Why tests are convenient
========================

The in-memory ``MappingStorage`` test path is a major ergonomic win over backends that
require a separate database daemon. Django's test runs can start with a fresh in-memory
store and avoid provisioning an external service entirely. More detail is in
:doc:`testing`.
