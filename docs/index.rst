.. _index:

===================
django-zodb-backend
===================

``django-zodb-backend`` is a Django database backend for `ZODB <https://zodb.org/>`_,
the Zope Object Database. The project is intentionally ambitious: run Django's ORM
and, ultimately, the Django test suite against an object database that has no SQL
engine at all.

The backend is modelled after ``django-mongodb-backend``, but it makes one critical
trade-off in the opposite direction: instead of translating Django's SQL abstract
syntax tree into another query language, it keeps Django's query construction intact
and performs execution directly against ZODB data structures.

.. important::

   This project is currently a proof of concept / pre-alpha backend. The design is
   serious, but the implementation is still growing toward broader ORM and test-suite
   coverage.

Start here:

* :doc:`getting-started` for installation and a minimal configuration.
* :doc:`architecture` for the storage model and query execution pipeline.
* :doc:`comparison` for the detailed comparison with ``django-mongodb-backend``.
* :doc:`django-fork` for the expected Django fork changes on the ``zodb-6.0.x`` branch.
* :doc:`release-process` for branch policy and the automated Django fork rebase workflow.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   getting-started
   architecture
   comparison
   decisions
   django-fork
   storage-backends
   testing
   release-process
   roadmap
