.. _architecture:

============
Architecture
============

This backend adapts Django's ORM to ZODB without pretending ZODB is a relational
database. Instead, it preserves Django's higher-level machinery and replaces SQL
execution with Python-level evaluation over persistent object containers.

Storage model
=============

At the root of the database is a ``PersistentMapping``. Each Django model table is stored
under its ``db_table`` name as a ``BTrees.LOBTree.LOBTree`` mapping integer primary keys
to persistent row objects.

A representative layout looks like this:

.. code-block:: text

   root (PersistentMapping) = {
       "auth_user": LOBTree {pk: PersistentMapping({...fields})},
       "auth_group": LOBTree {pk: PersistentMapping({...fields})},
       "__seq_auth_user": Length(42),
       "__seq_auth_group": Length(7),
       "__meta_auth_user": PersistentMapping({
           "indexes": {
               "auth_user_username_idx": {"columns": ["username"], "unique": True}
           }
       }),
       "__idx_auth_user_auth_user_username_idx": OOBTree {
           "alice": LLTreeSet({1}),
       }
   }

This yields three important implementation properties:

* rows are addressed by 64-bit integer primary key,
* table creation is just root-object bookkeeping,
* secondary indexes can live beside table data rather than in a separate engine.

Primary-key generation
======================

ZODB does not ship with an SQL-style sequence generator, so the backend maintains a
per-table ``BTrees.Length.Length`` object at ``root["__seq_<table>"]``.

.. code-block:: text

   __seq_auth_user -> Length(42)

On each insert, the counter is incremented and the resulting integer becomes the new
primary key. ``Length`` is a particularly good fit because it participates in ZODB's
conflict-resolution story, making simple counter increments safer under optimistic
concurrency. This decision is discussed in :doc:`decisions`.

Query execution: lazy SQL, eager ZODB
=====================================

The central design choice is best summarized as:

.. epigraph::

   Let Django build SQL-shaped queries lazily, then execute them eagerly against ZODB.

The flow is:

#. Django builds its normal ``Query`` / SQL AST structures.
#. The backend compiler receives the compiled query.
#. Instead of issuing SQL, the compiler iterates the model's ``LOBTree`` values.
#. The ``WHERE`` tree is evaluated as Python predicates against each object.
#. Ordering and slicing are applied in Python.

This is very different from ``django-mongodb-backend``, which translates Django's SQL AST
into MongoDB Query Language and aggregation pipelines. The comparison is explored in
:doc:`comparison`.

Current compiler behavior
-------------------------

The prototype compiler handles common lookup classes such as:

* ``exact`` / ``iexact``
* ``in`` / ``isnull``
* comparison operators (``gt``, ``gte``, ``lt``, ``lte``)
* ranges
* substring and prefix/suffix lookups
* regular expressions

The key implementation advantage is conceptual simplicity. The main cost is performance:
queries currently walk candidate rows in Python instead of using a native query planner.

.. warning::

   The current execution strategy is intentionally optimized for correctness and ORM
   compatibility, not for large-data performance.

Production-oriented path
------------------------

A more mature backend would tighten execution around secondary BTrees:

* maintain indexes for common field lookups,
* perform BTree range scans instead of full collection scans,
* intersect precomputed key sets for multi-condition predicates,
* reserve Python predicate evaluation for residual filters.

That staged path is deliberate: it keeps the proof of concept small while leaving room
for a serious storage engine strategy later.

Schema editing and migrations
=============================

Schema operations are lightweight because ZODB is schema-free:

``create_model()``
   Create the table ``LOBTree``.

``delete_model()``
   Remove the table ``LOBTree`` and related sequence metadata.

``add_field()`` / ``remove_field()`` / ``alter_field()``
   No-op from the storage layer's point of view.

``add_index()`` / ``remove_index()``
   Create or drop sidecar index metadata and index containers.

The practical effect is that schema evolution behaves more like Python object evolution
than SQL DDL. Old objects may simply lack a newly added attribute.

Transactions and savepoints
===========================

ZODB uses the ``transaction`` package and optimistic concurrency (MVCC). The backend maps
Django's transaction hooks onto ZODB's primitives:

* ``atomic()`` boundaries end in ``transaction.commit()`` or ``transaction.abort()``
* savepoints map naturally to ``transaction.manager.savepoint()``
* write conflicts surface as ``ZODB.POSException.ConflictError``

.. important::

   ZODB connections are not global shared cursors. The backend keeps a shared
   ``ZODB.DB`` per Django database alias and opens a ZODB connection per thread.

Why there is still a cursor object
==================================

Django expects every backend to present cursor-shaped APIs even when it never truly uses
SQL. ``django-zodb-backend`` therefore exposes a small cursor stub that satisfies Django
internals while rejecting raw SQL execution.

That is why the backend can participate in management commands and migration machinery
without pretending that ``cursor.execute()`` is meaningful.
