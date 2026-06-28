.. _roadmap:

=======
Roadmap
=======

This page summarizes what the project already has and what still needs to happen for a
credible full-suite Django backend.

Implemented in the current prototype
====================================

Core backend surface
--------------------

* ``DatabaseWrapper`` integration with Django's backend API,
* per-alias ``ZODB.DB`` pooling with per-thread connections,
* collection creation and deletion,
* integer primary-key allocation via ``BTrees.Length.Length``,
* transaction commit/rollback wiring,
* a cursor stub for Django internals.

ORM execution basics
--------------------

* Python-level evaluation of common ``WHERE`` lookups,
* ordering and slicing in the compiler,
* insert, update, delete, and aggregate compiler classes,
* basic value adaptation in ``DatabaseOperations``.

Schema and metadata
-------------------

* lightweight schema editor semantics,
* sidecar index metadata structures,
* test-database creation via in-memory storage,
* backend feature flags and explicit test skips.

Near-term priorities
====================

* expand Django ORM lookup coverage,
* harden aggregation and relation behavior,
* broaden the green subset of the Django test suite,
* turn more failures into either fixes or precisely justified skips,
* maintain and document the ``zodb-6.0.x`` Django fork.

Performance-oriented work
=========================

* populate and maintain secondary BTrees on writes,
* use indexed key discovery instead of full scans,
* support BTree set intersection for multi-condition queries,
* tighten uniqueness and constraint enforcement,
* reduce Python-level post-processing where possible.

Storage and deployment work
===========================

* broaden exercised coverage for ``file`` and ``zeo`` deployments,
* complete RelStorage integration and document its operational profile,
* improve multi-process and conflict-handling test coverage.

Long-term goal
==============

The long-term goal is not merely to have a novelty backend. It is to show that Django's
ORM abstraction is strong enough to drive a serious object-database backend with a clear,
understandable implementation strategy.

.. important::

   Success should be measured in two dimensions: increasing Django compatibility and a
   plausible path from proof of concept to indexed, production-aware execution.
