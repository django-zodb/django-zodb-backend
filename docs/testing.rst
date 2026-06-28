.. _testing:

=======
Testing
=======

The project is built around a very specific success metric: make more of Django's own
test suite run cleanly against ZODB over time.

How tests are run
=================

This repository includes a dedicated runner in ``.github/workflows/runtests.py`` and test
settings in ``.github/workflows/zodb_settings.py``.

**Local — run everything:**

.. code-block:: bash

   cd path/to/django_fork/tests/
   cp /path/to/django-zodb-backend/.github/workflows/zodb_settings.py .
   cp /path/to/django-zodb-backend/.github/workflows/runtests.py runtests_.py
   python runtests_.py

**Local — simulate a specific CI shard:**

.. code-block:: bash

   DJANGO_TEST_SHARD=2 DJANGO_TEST_SHARDS=8 python runtests_.py

**Local — run a single app directly:**

.. code-block:: bash

   python path/to/django/tests/runtests.py basic --settings zodb_settings -v 2

CI sharding — why it matters
=============================

Django's full test suite covers ~146 apps. Running them one-by-one in separate
subprocesses (the approach used by ``django-mongodb-backend``) means:

* 146 Python interpreter startups,
* 146 Django ``setup()`` calls,
* typical wall-clock time: **60–90 minutes**.

We take a different approach: **matrix sharding without** ``--parallel``.

.. code-block:: text

   ┌─ GitHub Actions matrix ─────────────────────────────────────────────┐
   │  shard 0: apps[0], apps[8], apps[16], …  →  runtests.py (sequential)│
   │  shard 1: apps[1], apps[9], apps[17], …  →  runtests.py (sequential)│
   │  …                                                                   │
   │  shard 7: apps[7], apps[15], apps[23], … →  runtests.py (sequential)│
   └─────────────────────────────────────────────────────────────────────┘

Each shard passes **all its apps in a single** ``runtests.py`` call — one
startup, not N.

Why not ``--parallel``?
-----------------------

ZODB's ``MappingStorage`` is a pure-Python in-process dict. When Django forks
worker processes for ``--parallel``, each worker inherits the parent's
``ZODB.DB`` object, including its open connections and transaction state.
Savepoint isolation then breaks: BTree mutations in one worker's transaction
bleed into others, causing ``MultipleObjectsReturned`` and related failures.

The 8-shard matrix already runs shards in parallel on separate GitHub-hosted
runners, so omitting ``--parallel`` costs no extra wall-clock time.

.. list-table:: Comparison of CI strategies
   :header-rows: 1
   :widths: 25 30 20 25

   * - Strategy
     - Approach
     - Python starts
     - Estimated wall time
   * - mongodb-backend
     - 1 job, 146 subprocesses
     - 146
     - 60–90 min
   * - django-zodb (v1)
     - 1 job, 1 subprocess
     - 1
     - 30–45 min
   * - **django-zodb (current)**
     - **8 shards × 1 subprocess**
     - **1 per shard**
     - **~15–25 min**

.. note::

   ZODB's ``MappingStorage`` gives an additional advantage: no external service
   startup (no ``mongod``, no PostgreSQL). Each shard's in-memory DB is
   instantiated in microseconds.

Why test setup is simpler than MongoDB
======================================

Tests use in-memory ``MappingStorage`` through the backend's ``DatabaseCreation`` path.
The full setup sequence is:

1. ``create_test_db()`` calls ``switch_to_test_storage()`` — replaces the configured
   storage with an in-memory ``MappingStorage``. No disk I/O, no server process.
2. ``migrate --run-syncdb`` runs against the fresh in-memory store. Django's migration
   framework calls ``SchemaEditor.create_model()`` for each model, which creates the
   corresponding ``OOBTree`` in the ZODB root — exactly as ``manage.py migrate``
   would in a real deployment.
3. ``mark_expected_failures_and_skips()`` registers the feature-based skip/xfail
   markers from ``DatabaseFeatures.django_test_skips``.

The result is a fully-initialised, migration-consistent test database that took
milliseconds to create.

.. tip::

   Because schema creation goes through the same ``SchemaEditor`` path as production,
   running the Django test suite is a continuous integration test of the migration
   machinery itself — not just the ORM.

Contrast with MongoDB:
  ``django-mongodb-backend`` requires a real ``mongod`` process and explicitly
  suppresses migrations for most built-in apps with a large ``MIGRATION_MODULES``
  dict. Our backend requires neither.

ZEO-specific test job
=====================

The ZEO tests (``tests/test_zeo.py``) live in a **separate CI job** — ``test-zeo`` — for a
specific reason: each test fixture starts a fresh in-process ZEO server via ``ZEO.server()``,
which spins up an asyncio event loop. This adds ~10–15 seconds per test, making the
suite take ~2 minutes total. Isolating it means this never delays or blocks the main shards.

.. code-block:: text

   CI jobs (per push / PR)
   ├── test (matrix: 8 shards × 2 Python versions = 16 jobs)  ~8-12 min
   ├── test-zeo (matrix: 2 Python versions = 2 jobs)           ~2 min
   └── lint                                                     ~30 s

To run ZEO tests locally:

.. code-block:: bash

   pip install -e ".[zeo,dev]"
   python -m pytest tests/test_zeo.py -v

ZEO tests cover:

* connectivity and roundtrip through the ZEO protocol layer,
* data committed by client A visible to independent client B (the core ZEO guarantee),
* ``FileStorage``-backed ZEO persistence across server restart,
* BTree concurrent writes from two clients without conflicts,
* the ``server_sync`` stronger-consistency option,
* the Django ``DatabaseWrapper`` reading and writing data via ZEO storage.

No external ``runzeo`` process is needed — ``ZEO.server()`` manages an in-process
server on a random port, making the tests fully self-contained.

Currently skipped areas
=======================

The backend declares explicit Django test skips for unsupported features. As of the current
prototype, the named skip categories are:

* raw query tests,
* ``select_for_update()`` tests,
* window-function tests,
* ``DISTINCT ON`` tests,
* ``QuerySet.extra()`` tests,
* GIS tests.

These skips are exposed through ``DatabaseFeatures.django_test_skips`` and should map
closely to the Django fork work described in :doc:`django-fork`.

What "passing" means today
==========================

The project should be understood in phases:

1. a targeted subset of Django apps is selected,
2. unsupported SQL-only areas are explicitly skipped,
3. remaining failures indicate genuine backend gaps.

So, in the current phase, "passing" does not mean the entire Django suite is green. It
means the selected scope is progressively becoming green without hiding unsupported areas.

Validation workflow for backend development
===========================================

A sensible loop for contributors is:

#. run one focused Django test app,
#. inspect whether failures are real bugs or unsupported features,
#. update backend code or fork patches accordingly,
#. rerun the same focused scope before expanding outward.

This matches the project's incremental design philosophy from :doc:`decisions`.
