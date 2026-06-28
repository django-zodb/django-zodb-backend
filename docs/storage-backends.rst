.. _storage-backends:

================
Storage backends
================

ZODB separates the object database API from the physical storage layer. That makes
``django-zodb-backend`` unusually flexible compared to a backend tied to a single server
process.

Overview
========

The project is designed around four storage modes exposed through ``DATABASES[...]["OPTIONS"]``:

* ``memory`` for ``MappingStorage``
* ``file`` for ``FileStorage``
* ``zeo`` for ``ClientStorage``
* ``relstorage`` for RelStorage-backed deployments

.. important::

   The current prototype code path directly implements ``memory``, ``file``, and ``zeo``.
   RelStorage is part of the intended configuration surface and optional dependency story,
   but may require additional wiring as the backend matures.

MappingStorage (``memory``)
===========================

``MappingStorage`` keeps the entire database in memory in the current process.

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "devdb",
           "OPTIONS": {
               "storage": "memory",
           },
       }
   }

Use cases:

* test runs,
* CI,
* local experimentation,
* backend development.

Why it matters
--------------

This is one of the backend's biggest practical advantages. Tests can run with a fresh,
disposable database and no external service. See :doc:`testing`.

FileStorage (``file``)
======================

``FileStorage`` writes ZODB data to a ``.fs`` append-log file.

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "site",
           "OPTIONS": {
               "storage": "file",
               "PATH": "var/site.fs",
           },
       }
   }

Characteristics:

* simple deployment,
* durable local persistence,
* single-writer bias,
* good fit for experiments and small single-process services.

.. warning::

   ``FileStorage`` is not the answer for every concurrency problem. If multiple processes
   need coordinated access, evaluate ZEO or another deployment architecture.

ZEO ClientStorage (``zeo``)
===========================

ZEO adds a storage server in front of ZODB and allows multiple client processes to work
with the same object database.

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "site",
           "OPTIONS": {
               "storage": "zeo",
               "HOST": "127.0.0.1",
               "PORT": 8001,
           },
       }
   }

Use ``zeo`` when:

* multiple Django worker processes must share the same store,
* you want the classic ZODB networked deployment model,
* you need to separate storage management from application processes.

RelStorage (``relstorage``)
===========================

RelStorage stores ZODB pickles inside a relational database such as PostgreSQL, MySQL,
or SQLite while preserving ZODB's object semantics.

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "site",
           "OPTIONS": {
               "storage": "relstorage",
               "URL": "postgresql://user:pass@localhost/site",
           },
       }
   }

Important nuance
----------------

RelStorage does **not** turn ``django-zodb-backend`` into a relational Django backend.
The relational database is merely the persistence layer for ZODB objects. Query execution
still follows the ZODB path described in :doc:`architecture`; it does not suddenly gain
SQL query execution for Django ORM statements.

Operational guidance
====================

Choose storage by lifecycle stage:

``memory``
   Best default for tests and proof-of-concept work.

``file``
   Best simple durable option for single-node experiments.

``zeo``
   Best classic multi-process ZODB deployment path.

``relstorage``
   Best when operational requirements favor a relational persistence substrate for ZODB.

Threading note
==============

Regardless of storage backend, each thread should have its own ZODB connection opened
from the shared ``ZODB.DB`` object. That rule remains the same across storage choices.
