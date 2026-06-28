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

Typical usage:

.. code-block:: bash

   python .github/workflows/runtests.py

That runner currently exercises a curated subset of Django test apps, including areas
such as:

* core ORM basics,
* model infrastructure,
* relations,
* queries and aggregation,
* auth,
* migrations,
* validation and model forms.

A targeted app can also be run directly with Django's upstream ``runtests.py`` pattern:

.. code-block:: bash

   python path/to/django/tests/runtests.py basic --settings zodb_settings -v 2

Why test setup is simpler than MongoDB
======================================

Tests use in-memory ``MappingStorage`` through the backend's ``DatabaseCreation`` path.
That means:

* no external server process,
* no separate test database creation step,
* a fresh in-memory store for each test run.

.. tip::

   This is one of the clearest quality-of-life wins of the ZODB approach: contributors can
   focus on backend behavior instead of database service orchestration.

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
