.. _storage-backends:

================
Storage backends
================

ZODB separates the object database API from the physical storage layer. That makes
``django-zodb-backend`` unusually flexible compared with a backend tied to a single
server process.

Overview
========

The storage backend is selected automatically from the settings you provide — no
explicit ``"storage"`` key is needed:

* ``OPTIONS["PATH"]`` set, no ``HOST`` → ``FileStorage`` (single-process, durable)
* ``HOST`` set in ``DATABASES``       → ZEO ``ClientStorage`` (multi-process)
* nothing set                         → ``MappingStorage`` (in-memory, tests/CI only)

FileStorage
===========

``FileStorage`` writes ZODB data to a ``.fs`` append-log file.  This is the
standard choice for single-node development and production deployments.

Set ``PATH`` in ``OPTIONS`` and leave ``HOST`` unset:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "site",
           "OPTIONS": {
               "PATH": "var/site.fs",
           },
       }
   }

Characteristics:

* simple deployment — one file, no separate process,
* durable local persistence,
* single-writer bias (appropriate for single-process deployments),
* good fit for experiments, small services, and development.

.. warning::

   ``FileStorage`` is not the answer for every concurrency requirement.  If
   multiple processes need coordinated write access, use ZEO.

ZEO ClientStorage
=================

ZEO adds a storage server in front of ZODB and allows multiple client processes to
work with the same object database.  This is the standard ZODB multi-process
deployment pattern, directly analogous to running separate application workers against
a shared database server.

Set ``HOST`` (and optionally ``PORT``) as top-level ``DATABASES`` settings:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "site",
           "HOST": "127.0.0.1",
           "PORT": "8001",
       }
   }

You can also embed the port directly in ``HOST``:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "site",
           "HOST": "127.0.0.1:8001",
       }
   }

All available settings for ZEO
-------------------------------

``HOST`` and ``PORT`` live at the top level of the ``DATABASES`` entry (standard
Django convention). The remaining ZEO-specific options go in ``OPTIONS``:

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Setting
     - Default
     - Description
   * - ``HOST``
     - ``localhost``
     - ZEO server hostname, IP, or ``"host:port"``.  Ignored when ``OPTIONS["PATH"]`` is set.
   * - ``PORT``
     - ``8001``
     - ZEO server TCP port (**top-level**, not in OPTIONS).  Ignored when ``OPTIONS["PATH"]`` is set.  Takes precedence over a port embedded in ``HOST``.
   * - ``OPTIONS["PATH"]``
     - —
     - Unix socket path (overrides ``HOST``/``PORT``).
   * - ``OPTIONS["wait_timeout"]``
     - ``30``
     - Seconds to wait for the ZEO server to become available on connect.
   * - ``OPTIONS["read_only"]``
     - ``False``
     - Open a read-only connection (cannot commit).
   * - ``OPTIONS["server_sync"]``
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
       "PATH": "/run/myapp/zeo.sock",
   }

.. code-block:: bash

   runzeo -a /run/myapp/zeo.sock -f /var/lib/myapp/data.fs

.. _server_sync:

``server_sync`` — stronger read consistency
--------------------------------------------

By default ZEO clients use their local object cache and may lag slightly behind
the latest commit from another client.  Setting ``server_sync: True`` makes the
client call ``serverSync()`` before each read transaction, ensuring it always
sees the most recently committed state:

.. code-block:: python

   "HOST": "127.0.0.1",
   "PORT": "8001",
   "OPTIONS": {
       "server_sync": True,   # stronger consistency, one extra RPC per read
   }

.. note::

   ``server_sync`` adds a round-trip to the ZEO server on every read.  Use it
   when read-your-own-writes guarantees are required across workers; leave it
   off otherwise.

Use ZEO when:

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
random port, so no external ``runzeo`` process is required for CI.  See
:doc:`testing` for how the ZEO CI job is configured.

MappingStorage (memory)
=======================

``MappingStorage`` keeps the entire database in memory in the current process.
No ``HOST`` and no ``OPTIONS["PATH"]`` → memory storage is selected automatically:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django_zodb_backend",
           "NAME": "devdb",
       }
   }

Use cases:

* test runs,
* CI,
* local experimentation,
* backend development.

.. important::

   Data does not survive process exit.  Do not use memory storage for
   anything you want to keep.

This is one of the backend's biggest practical advantages for testing.  Tests can
run with a fresh, disposable database and no external service.  See :doc:`testing`.

Operational guidance
====================

Choose storage by what settings you provide:

``OPTIONS["PATH"]`` set, no ``HOST``
   FileStorage — normal development and single-node production.

``HOST`` set
   ZEO ClientStorage — multi-process production or staging environments.

Neither set
   MappingStorage — tests and CI only.

Threading note
==============

Regardless of storage backend, each thread should have its own ZODB connection
opened from the shared ``ZODB.DB`` object.  That rule remains the same across
storage choices.
