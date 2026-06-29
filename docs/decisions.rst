.. _decisions:

========================
Architectural decisions
========================

This page records the major design decisions behind ``django-zodb-backend`` in an
ADR-style format.

Decision 1: keep integer primary keys
=====================================

Status
------
Accepted.

Context
-------

A non-SQL backend must decide whether to preserve Django's default integer primary keys
or adopt a datastore-native identifier type.

MongoDB chose datastore-native ``ObjectId`` values, which forced significant Django test
fork changes. ZODB does not create that pressure because ``OOBTree`` accepts integer keys
natively and they map efficiently to B-tree storage.

Decision
--------

Use ``BigAutoField`` semantics and assign keys from a per-table ``BTrees.Length.Length``
counter stored in the ZODB root at ``__seq_<table>``.

Consequences
------------

Positive:

* Django's default PK assumptions remain intact.
* ``GenericForeignKey.object_id`` fields stay integer-based.
* Fixture and test-fork churn is reduced significantly.
* Developer ergonomics improve because IDs look like normal Django IDs.

Negative:

* The backend must provide its own auto-increment strategy.
* PK generation is a backend concern rather than a storage-native feature.

Decision 2: prefer lazy SQL construction and eager object execution
===================================================================

Status
------
Accepted for the proof-of-concept phase.

Context
-------

Django internally builds SQL-shaped query objects even for custom backends. A backend can
try to translate those queries into another datastore language, or it can defer execution
until it has enough information to evaluate the query directly.

ZODB has no query language worth targeting. Building one in the backend would be more
complex than the storage engine itself.

Decision
--------

Allow Django to build its SQL AST normally, then evaluate the resulting predicates in
Python against objects loaded from the table ``OOBTree``.

Consequences
------------

Positive:

* much simpler prototype compiler,
* maximal reuse of Django ORM query construction,
* fewer moving parts to debug while aiming at test-suite compatibility.

Negative:

* full scans are common in the current prototype,
* complex ORM features may require incremental evaluator work,
* performance work is deferred to a later milestone.

Decision 3: use MappingStorage as the test default
==================================================

Status
------
Accepted.

Context
-------

A backend intended to run the Django test suite should minimize external infrastructure.
One of ZODB's biggest advantages is that it can run entirely in process.

Decision
--------

Switch tests to in-memory ``MappingStorage`` by default and treat it as the baseline test
backend.

Consequences
------------

Positive:

* no separate test database daemon,
* easy setup in CI,
* fast and disposable test environments,
* simpler contributor experience.

Negative:

* the default test path does not exercise multi-process storage concerns,
* storage-specific bugs may be hidden until ``file`` or ``zeo`` backends are tested.

Decision 4: treat most schema edits as metadata operations
==========================================================

Status
------
Accepted.

Context
-------

ZODB stores Python objects directly. Adding a Django field does not require rewriting an
on-disk table definition.

Decision
--------

Implement ``create_model()`` and ``delete_model()`` as real BTree operations, but
make field add/remove/alter operations no-ops at the storage layer.

Consequences
------------

Positive:

* migrations remain cheap,
* schema evolution follows Python object compatibility patterns,
* the backend avoids fake DDL work.

Negative:

* storage-level constraints are weaker,
* code must tolerate missing attributes on older objects,
* some SQL-era migration expectations do not apply.

Decision 5: represent indexes as sidecar BTrees
================================================

Status
------
Accepted as the long-term indexing strategy; partially implemented in the prototype.

Context
-------

Query execution cannot stay scan-heavy forever if the backend is to mature. ZODB's BTree
family makes sidecar index structures a natural choice.

Decision
--------

Store index metadata under ``__meta_<table>`` and maintain index BTrees under
``__idx_<table>_<index_name>``.

Consequences
------------

Positive:

* index structures are explicit and inspectable,
* the design aligns with ZODB's native containers,
* multi-condition queries can evolve toward set intersection.

Negative:

* write paths become more complex once index maintenance is fully enforced,
* uniqueness and constraint semantics must be implemented carefully in Python.

Decision 6: map Django transactions onto ZODB transactions directly
===================================================================

Status
------
Accepted.

Context
-------

Django expects a backend to integrate with ``atomic()``, rollbacks, and savepoints.
ZODB already exposes transactions through the Python ``transaction`` package.

Decision
--------

Map commit and rollback directly to ``transaction.commit()`` and
``transaction.abort()``. Rely on native ZODB savepoint support.

Consequences
------------

Positive:

* strong conceptual alignment,
* no need for a fake transaction layer,
* savepoints are available immediately.

Negative:

* callers must still understand optimistic conflict behavior,
* conflict handling needs disciplined testing as concurrency support expands.

.. seealso::

   :doc:`architecture` for implementation details and :doc:`comparison` for how these
   choices differ from ``django-mongodb-backend``.
