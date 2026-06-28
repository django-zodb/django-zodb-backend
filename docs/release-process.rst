.. _release-process:

===============
Release process
===============

.. note::

   This document mirrors the approach described in
   `django-mongodb-backend's release process
   <https://django-mongodb-backend.readthedocs.io/en/latest/internals/release-process/>`_
   and adapts it for the ZODB backend.

Supported versions
==================

``django-zodb-backend`` follows `Django's supported versions policy
<https://docs.djangoproject.com/en/stable/internals/release-process/#supported-versions-policy>`_.

The ``main`` branch tracks the most recent Django feature release.
Security fixes and data-loss bugs are also applied to the previous feature
release branch.

.. list-table:: Current branch tracking
   :header-rows: 1
   :widths: 30 70

   * - Branch
     - Tracks
   * - ``main``
     - Latest Django feature release (currently 6.0.x)
   * - ``5.2.x`` *(planned)*
     - Django 5.2 LTS

Branch policy
=============

After a new Django feature release (6.1, 7.0, …):

1. A maintenance branch is cut from ``main`` to track the previous release
   (e.g. ``5.2.x`` was cut when Django 6.0 shipped).
2. ``main`` is updated to track the new feature release.
3. A "Add support for Django X.Y" pull request is opened to formalise the
   upgrade.

The Django fork
===============

Because Django's test suite is designed for SQL backends, running it against
ZODB requires a small number of modifications that cannot be contributed
upstream. These are maintained in `django-zodb/django
<https://github.com/django-zodb/django>`_.

Each Django feature release has a corresponding branch in the fork:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Fork branch
     - Tracks
   * - ``zodb-6.0.x``
     - ``django/django`` ``stable/6.0.x``

What the fork changes
---------------------

Unlike the MongoDB fork (which had to change integer PKs to ``ObjectId``
throughout), the ZODB fork changes are minimal because ``BigAutoField``
(64-bit integer PKs) is the correct default for ZODB:

* Remove or skip SQL-specific assertions that can never pass against a
  non-SQL backend.
* Adjust test settings (storage, runner, sharding) for the ZODB backend.
* Any further changes required by :doc:`django-fork`.

Automated rebase workflow
=========================

The ``rebase-upstream`` GitHub Actions workflow (in `django-zodb/django
<https://github.com/django-zodb/django/blob/zodb-6.0.x/.github/workflows/rebase-upstream.yml>`_)
keeps the fork branch in sync with upstream Django automatically.

**Schedule:** weekly, Monday 09:00 UTC (and manually via ``workflow_dispatch``).

**What it does:**

1. Checks out ``django-zodb/django`` on the fork branch (e.g. ``zodb-6.0.x``).
2. Adds ``django/django`` as an upstream remote.
3. Rebases the fork branch onto the upstream stable branch.
4. If the rebase introduces new commits, pushes to a dated PR branch
   (``rebase/zodb-6.0.x-YYYY-MM-DD``) and opens a pull request.
5. If a rebase PR is already open, skips to avoid spam.
6. If the rebase produces conflicts, the workflow fails with an error and
   a human must resolve them manually.

**Adding a new branch pair:**

Extend the ``matrix`` in ``.github/workflows/rebase-upstream.yml`` in the
`django-zodb/django <https://github.com/django-zodb/django>`_ fork:

.. code-block:: yaml

   matrix:
     include:
       - fork_branch: zodb-6.0.x
         upstream_branch: stable/6.0.x
       - fork_branch: zodb-6.1.x       # add when Django 6.1 ships
         upstream_branch: stable/6.1.x

**Reviewing a rebase PR:**

Before merging a rebase PR, check that:

* No unintended conflicts were silently accepted during rebase.
* The ``tests`` workflow passes against the rebased branch.
* Any new Django commits that touch test infrastructure (new test models,
  new test apps, changed helper assertions) are reflected in
  ``django-zodb-backend``'s skip list or compiler fixes if needed.

**Dry run:**

Trigger the workflow manually from `django-zodb/django
<https://github.com/django-zodb/django/actions>`_ and set the ``dry_run``
input to ``true`` to see what the rebase would produce without pushing:

.. code-block:: bash

   gh workflow run rebase-upstream.yml \
     --repo django-zodb/django \
     -f dry_run=true
