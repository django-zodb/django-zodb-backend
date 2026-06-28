.. _comparison:

=========================================
Comparison with django-mongodb-backend
=========================================

``django-zodb-backend`` is explicitly modelled after ``django-mongodb-backend``. The two
projects share a strategic goal—running Django on a non-SQL datastore—but they diverge in
several foundational ways.

Executive summary
=================

Both backends must solve the same broad problems:

* map Django models onto non-relational storage,
* decide how primary keys behave,
* integrate with migrations and transactions,
* determine which parts of the Django test suite need patching or skipping.

The biggest difference is that MongoDB comes with its own query language, while ZODB does
not. MongoDB therefore pushes the backend toward query compilation. ZODB pushes the
backend toward direct object traversal.

Primary-key strategy: ObjectId vs integer
=========================================

This is the most consequential architectural difference.

MongoDB
-------

``django-mongodb-backend`` uses MongoDB's ``ObjectId`` as the default primary key. That
choice fits MongoDB naturally, but it ripples into Django compatibility work:

* ``GenericForeignKey.object_id`` fields in Django's test suite must move from integer
  fields to ``TextField``/``CharField``.
* default-primary-key assertions must expect ``ObjectIdAutoField`` rather than
  ``BigAutoField``.
* fixture files and hardcoded PK expectations need updating.
* project-wide settings need ``DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"``.

Those changes are exactly why the MongoDB project carries a substantial Django fork.

ZODB
----

ZODB is a much better fit for Django's normal integer-key assumptions. ``BTrees.LOBTree``
uses 64-bit integer keys, so the backend can keep Django's standard ``BigAutoField``.
That yields immediate benefits:

* no ``GenericForeignKey.object_id`` type changes,
* no global ``DEFAULT_AUTO_FIELD`` override,
* no fixture rewrites for opaque IDs,
* far fewer hardcoded-PK test changes.

In short: both projects need a Django fork, but ZODB needs a much smaller one.

Auto-increment behavior
-----------------------

MongoDB's primary-key generation follows ``ObjectId`` semantics. ZODB instead uses a
``BTrees.Length.Length`` object per table under ``__seq_<table>``. That delivers a
monotonic integer key path that is both easy for Django to consume and compatible with
ZODB's optimistic write model.

Django fork impact
==================

The MongoDB fork (``mongodb-forks/django`` on ``mongodb-6.0.x``) had to absorb a wide
range of adaptations, including:

* removing hardcoded integer-PK assertions,
* changing ``object_id`` fields to text,
* adding ``skipUnlessDBFeature`` decorators,
* commenting out SQL-only assertions such as ``EXPLAIN`` and raw-SQL checks,
* replacing ``QuerySet.extra()`` usage,
* adapting window-expression and ``StringAgg`` tests,
* setting the MongoDB-specific default auto field in settings.

For the ZODB fork (``django-zodb/django`` on ``zodb-6.0.x``), the required changes are
narrower:

* remove or soften SQL-specific assertions,
* add ``skipUnlessDBFeature`` coverage for unsupported areas,
* keep integer primary keys everywhere,
* keep Django's normal ``BigAutoField`` default.

See :doc:`django-fork` for the concrete checklist.

Query architecture
==================

MongoDB backend
---------------

``django-mongodb-backend`` is a genuine query compiler. Its compiler translates Django's
SQL AST into MongoDB Query Language and aggregation stages such as ``$match``, ``$group``
and ``$project``.

Advantages:

* leverages the database engine for filtering and grouping,
* scales better for indexed queries,
* has a recognizable compilation pipeline.

Costs:

* high implementation complexity,
* continual pressure to emulate SQL features over a different query language,
* more backend-specific logic in the compiler layer.

ZODB backend
------------

``django-zodb-backend`` takes the opposite route:

* let Django construct the SQL AST as usual,
* ignore SQL as an execution language,
* iterate ZODB collections directly,
* apply the ``WHERE`` tree as Python predicates.

Advantages:

* much smaller proof-of-concept surface,
* easy to reason about,
* avoids inventing a fake SQL dialect or full translation layer.

Costs:

* slower query execution,
* more Python-level scanning,
* production performance depends on follow-up index work.

.. note::

   The ZODB strategy is intentionally biased toward passing Django behavior first and
   optimizing later. It is not a claim that full scans are the end state.

Storage and test ergonomics
===========================

MongoDB generally implies a running ``mongod`` somewhere, even in development or CI.
ZODB has a simpler test story because ``MappingStorage`` is in-process and in-memory.

That difference matters a lot for the Django test suite:

* MongoDB must provision an external server.
* ZODB can create a fresh in-memory store per test run.
* No separate test-database process is required for ZODB.

Schema management
=================

Both systems are effectively schemaless from the relational perspective, but their
backend expressions differ.

MongoDB
   Backend schema editing still creates and drops collections and manages indexes.

ZODB
   ``create_model()`` creates a root-level collection, ``delete_model()`` removes it,
   and field addition/removal is a storage no-op because Python objects carry their own
   attributes.

Transactions
============

MongoDB and ZODB both support transactions, but the operational model differs.

MongoDB
   Uses database-native transaction semantics in a document database context.

ZODB
   Uses the Python ``transaction`` package, MVCC, and ``ConflictError`` handling.
   Savepoints are a native fit rather than an emulation layer.

What this means for the project
===============================

The comparison points to a broader conclusion:

* ``django-mongodb-backend`` is a larger and more database-native compiler project.
* ``django-zodb-backend`` is a smaller and more ORM-adapter-oriented execution project.

That difference is exactly why the ZODB proof of concept can move quickly on the Django
compatibility front while deferring heavy query optimization to later milestones.
