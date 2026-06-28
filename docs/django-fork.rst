.. _django-fork:

====================
Django fork strategy
====================

The backend's long-term goal is to run Django's own test suite. In practice, that means
maintaining a Django fork branch tailored to this backend, just as
``django-mongodb-backend`` maintains ``mongodb-forks/django``.

For ``django-zodb-backend``, the expected branch is ``django-zodb/django`` on
``zodb-6.0.x``.

Why a fork is still needed
==========================

Even though ZODB preserves integer primary keys, Django's test suite still contains a
number of assumptions that are specific to SQL backends:

* exact ``EXPLAIN`` output expectations,
* raw-SQL execution checks,
* SQL-only APIs such as ``select_for_update()`` and ``QuerySet.extra()``,
* backend-specific GIS and window-function coverage.

A fork lets the project mark those assumptions explicitly instead of carrying fragile
monkey patches in the backend itself.

MongoDB fork: what had to change
================================

The MongoDB branch had to do substantial compatibility work, including:

* remove hardcoded integer-PK assertions,
* change ``generic_relations/models.py`` to ``object_id = models.TextField()``,
* update ``model_options/test_default_pk.py`` to assert ``ObjectIdAutoField``,
* rewrite fixture expectations around non-integer primary keys,
* remove or replace ``QuerySet.extra()`` usage,
* adapt window-expression and ``StringAgg`` tests,
* set ``DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"`` in settings,
* add ``skipUnlessDBFeature`` decorators for unsupported areas,
* comment out SQL-specific assertions around raw SQL and ``EXPLAIN``.

ZODB fork: what should change
=============================

The ZODB branch can be much smaller.

Required changes
----------------

The ``zodb-6.0.x`` branch should focus on these categories:

* remove or relax SQL-specific assertions,
* add ``skipUnlessDBFeature`` decorators for unsupported features,
* preserve integer primary-key expectations,
* preserve ``DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"``.

Concretely, expect patches in areas such as:

* raw query tests,
* ``select_for_update`` tests,
* window-function tests,
* ``distinct_on_fields`` tests,
* ``extra_regress`` tests,
* GIS test modules,
* SQL ``EXPLAIN`` assertions,
* any tests that directly inspect generated SQL strings.

Notably absent from the ZODB fork
---------------------------------

These MongoDB-specific changes should **not** be necessary:

* changing ``GenericForeignKey.object_id`` to text,
* replacing integer PK fixtures with opaque identifiers,
* changing default PK assertions to a custom auto field,
* globally overriding ``DEFAULT_AUTO_FIELD`` for datastore-native IDs.

Recommended patching approach
=============================

Use Django's own backend-feature mechanisms wherever possible.

1. Express backend capabilities in ``DatabaseFeatures``.
2. Patch Django tests to use ``skipUnlessDBFeature`` instead of assuming SQL.
3. Remove assertions that only make sense for text SQL output.
4. Keep behavior assertions whenever they are backend-agnostic.

.. tip::

   The goal of the fork is not to weaken Django's test suite. It is to separate
   relational assumptions from ORM behavior so the backend is judged on the right axis.

Suggested checklist for ``zodb-6.0.x``
======================================

#. Audit every failure for one of three causes: unsupported SQL-only feature, true backend
   bug, or test assumption leak.
#. Convert assumption leaks into feature-guarded skips where appropriate.
#. Keep primary-key-related tests as close to upstream as possible.
#. Prefer narrow patches over broad rewrites.
#. Rebase regularly to keep the fork understandable.

Relationship to backend feature flags
=====================================

The repository's ``DatabaseFeatures.django_test_skips`` already points toward the first
wave of fork work. Today it names the following unsupported categories:

* raw queries,
* ``select_for_update()``,
* window functions,
* ``DISTINCT ON``,
* ``QuerySet.extra()``,
* GIS.

Those skip declarations should inform the corresponding Django fork patches.
