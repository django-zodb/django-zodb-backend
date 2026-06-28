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
with the same object database. This is the standard ZODB multi-process deployment pattern,
directly analogous to running separate application workers against a shared database server.

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

All available ``OPTIONS`` keys for ZEO
---------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Key
     - Default
     - Description
   * - ``HOST``
     - ``localhost``
     - ZEO server hostname or IP. Ignored when ``PATH`` is set.
   * - ``PORT``
     - ``8001``
     - ZEO server TCP port. Ignored when ``PATH`` is set.
   * - ``PATH``
     - —
     - Unix socket path (overrides ``HOST``/``PORT``).
   * - ``wait_timeout``
     - ``30``
     - Seconds to wait for the ZEO server to become available on connect.
   * - ``read_only``
     - ``False``
     - Open a read-only connection (cannot commit).
   * - ``server_sync``
     - ``False``
     - Call ``serverSync()`` before each read; see :ref:`server_sync` below.

Running the ZEO server
-----------------------

In production, run the ZEO server as a separate long-lived process:

.. code-block:: bash

   # Using the runzeo command installed with the ZEO package:
   runzeo -a localhost:8001 -f /var/lib/myapp/data.fs

   # Or with a ZConfig file for full control:
   runzeo -C /etc/myapp/zeo.conf

The ZEO server handles one physical ``.fs`` file and serialises all writes.
All Django worker processes connect as ZEO clients.

Unix sockets (same host)
-------------------------

When all workers are on the same machine, Unix sockets are faster than TCP:

.. code-block:: python

   "OPTIONS": {
       "storage": "zeo",
       "PATH": "/run/myapp/zeo.sock",
   }

.. code-block:: bash

   runzeo -a /run/myapp/zeo.sock -f /var/lib/myapp/data.fs

.. _server_sync:

``server_sync`` — stronger read consistency
--------------------------------------------

By default ZEO clients use their local object cache and may lag slightly behind
the latest commit from another client. Setting ``server_sync: True`` makes the
client call ``serverSync()`` before each read transaction, ensuring it always
sees the most recently committed state:

.. code-block:: python

   "OPTIONS": {
       "storage": "zeo",
       "HOST": "127.0.0.1",
       "PORT": 8001,
       "server_sync": True,   # stronger consistency, one extra RPC per read
   }

.. note::

   ``server_sync`` adds a round-trip to the ZEO server on every read. Use it
   when read-your-own-writes guarantees are required across workers; leave it
   off otherwise.

Use ``zeo`` when:

* multiple Django worker processes (Gunicorn, uWSGI, etc.) must share one store,
* you want the classic ZODB networked deployment model,
* you need to separate storage management from application processes.

ZEO-specific tests
-------------------

The test suite includes a dedicated ``tests/test_zeo.py`` covering:

* connectivity and roundtrip reads/writes through the ZEO layer,
* data visibility across two independent ``ZODB.DB`` clients,
* FileStorage persistence across server restarts,
* BTree-level concurrent writes from multiple clients,
* the ``server_sync`` consistency guarantee,
* the Django ``DatabaseWrapper`` configured with ZEO storage.

These tests use ``ZEO.server()`` to start an **in-process** ZEO server on a
random port, so no external ``runzeo`` process is required for CI. See
:doc:`testing` for how the ZEO CI job is configured.

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
