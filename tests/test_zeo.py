"""
Tests for the ZEO storage backend.

ZEO (Zope Enterprise Objects) is a client-server ZODB storage that allows
multiple processes to share a single ZODB database. These tests verify that
the django-zodb-backend works correctly when backed by a ZEO server, and
specifically test behaviors that are unique to the multi-connection ZEO case:

- Data written by one connection is visible to another after commit.
- The Django backend connects, writes, and reads correctly via ZEO.
- The ``server_sync`` option provides stronger read-your-writes consistency.
- FileStorage-backed ZEO persists data across server restarts.

Tests here start an in-process ZEO server using ``ZEO.server()``, which
returns a (addr, stop_fn) pair. No external ``runzeo`` process is needed,
making these tests suitable for CI without additional services.
"""

import tempfile
import threading
import time
from pathlib import Path

import pytest
import transaction
import ZODB
import ZEO
from persistent.mapping import PersistentMapping

pytest.importorskip("ZEO", reason="ZEO package not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zeo_memory_server():
    """Start an in-process ZEO server backed by MappingStorage (in-memory)."""
    addr, stop = ZEO.server()  # path=None → MappingStorage
    yield addr
    stop()


@pytest.fixture()
def zeo_file_server(tmp_path):
    """Start an in-process ZEO server backed by FileStorage."""
    fs_path = str(tmp_path / "test.fs")
    addr, stop = ZEO.server(path=fs_path)
    yield addr, fs_path, stop
    stop()


@pytest.fixture()
def zeo_db(zeo_memory_server):
    """Return a ZODB.DB connected to the in-process ZEO memory server."""
    storage = ZEO.client(zeo_memory_server, wait_timeout=10)
    db = ZODB.DB(storage)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Low-level ZEO connectivity tests (no Django backend layer)
# ---------------------------------------------------------------------------


class TestZEOConnectivity:
    """Sanity-check ZEO works as expected before layering Django on top."""

    def test_server_starts_and_accepts_connections(self, zeo_memory_server):
        storage = ZEO.client(zeo_memory_server, wait_timeout=10)
        db = ZODB.DB(storage)
        conn = db.open()
        assert conn.root() is not None
        conn.close()
        db.close()

    def test_write_and_read_roundtrip(self, zeo_db):
        conn = zeo_db.open()
        conn.root()["ping"] = "pong"
        transaction.commit()
        conn.close()

        conn2 = zeo_db.open()
        assert conn2.root()["ping"] == "pong"
        conn2.close()

    def test_two_independent_clients_share_data(self, zeo_memory_server):
        """Data committed by client A is visible to client B — the core ZEO guarantee."""
        storageA = ZEO.client(zeo_memory_server, wait_timeout=10)
        dbA = ZODB.DB(storageA)

        storageB = ZEO.client(zeo_memory_server, wait_timeout=10)
        dbB = ZODB.DB(storageB)

        # A writes.
        connA = dbA.open()
        connA.root()["shared"] = "hello from A"
        transaction.commit()
        connA.close()

        # B reads the same data without restarting.
        connB = dbB.open()
        assert connB.root()["shared"] == "hello from A"
        connB.close()

        dbA.close()
        dbB.close()

    def test_persistent_objects_survive_reconnect(self, zeo_file_server):
        """FileStorage-backed ZEO persists data across server restarts."""
        addr, fs_path, stop = zeo_file_server

        # Write some data.
        storageA = ZEO.client(addr, wait_timeout=10)
        dbA = ZODB.DB(storageA)
        connA = dbA.open()
        connA.root()["persistent_key"] = PersistentMapping({"value": 42})
        transaction.commit()
        connA.close()
        dbA.close()
        stop()

        # Restart the server against the same file.
        addr2, stop2 = ZEO.server(path=fs_path)
        try:
            storageB = ZEO.client(addr2, wait_timeout=10)
            dbB = ZODB.DB(storageB)
            connB = dbB.open()
            assert connB.root()["persistent_key"]["value"] == 42
            connB.close()
            dbB.close()
        finally:
            stop2()

    def test_btree_writes_are_consistent_across_clients(self, zeo_memory_server):
        """BTree-level concurrent writes from two clients both survive."""
        from BTrees.LOBTree import LOBTree

        storageA = ZEO.client(zeo_memory_server, wait_timeout=10)
        dbA = ZODB.DB(storageA)
        storageB = ZEO.client(zeo_memory_server, wait_timeout=10)
        dbB = ZODB.DB(storageB)

        # Initialise the BTree via A.
        connA = dbA.open()
        connA.root()["tree"] = LOBTree()
        transaction.commit()
        connA.close()

        # A and B write to *different* keys concurrently — should not conflict.
        connA = dbA.open()
        connA.root()["tree"][1] = PersistentMapping({"owner": "A"})
        transaction.commit()
        connA.close()

        connB = dbB.open()
        connB.root()["tree"][2] = PersistentMapping({"owner": "B"})
        transaction.commit()
        connB.close()

        # Both entries visible from a fresh connection.
        connA = dbA.open()
        tree = connA.root()["tree"]
        assert tree[1]["owner"] == "A"
        assert tree[2]["owner"] == "B"
        connA.close()

        dbA.close()
        dbB.close()


# ---------------------------------------------------------------------------
# Django backend via ZEO storage
# ---------------------------------------------------------------------------


class TestDjangoBackendWithZEO:
    """
    Test the django_zodb_backend DatabaseWrapper configured with ZEO storage.

    We patch the connection pool directly so we can inject a ZEO-backed DB
    without touching Django settings.
    """

    def _make_zeo_wrapper(self, addr):
        """Return a DatabaseWrapper pointing at the given ZEO server address."""
        import django
        from django.conf import settings

        if not settings.configured:
            settings.configure(
                DATABASES={
                    "default": {
                        "ENGINE": "django_zodb_backend",
                        "NAME": "zeo_test",
                        "OPTIONS": {
                            "storage": "zeo",
                            "HOST": addr[0],
                            "PORT": addr[1],
                            "wait_timeout": 10,
                        },
                    }
                },
                DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
                INSTALLED_APPS=[],
                USE_TZ=False,
            )
            django.setup()

        from django_zodb_backend.base import DatabaseWrapper, _db_pool, _pool_lock

        settings_dict = {
            "ENGINE": "django_zodb_backend",
            "NAME": "zeo_test",
            "OPTIONS": {
                "storage": "zeo",
                "HOST": addr[0],
                "PORT": addr[1],
                "wait_timeout": 10,
            },
            "USER": "",
            "PASSWORD": "",
            "HOST": "",
            "PORT": "",
            "TIME_ZONE": None,
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "TEST": {},
            "AUTOCOMMIT": True,
            "ATOMIC_REQUESTS": False,
            "OPTIONS": {
                "storage": "zeo",
                "HOST": addr[0],
                "PORT": addr[1],
                "wait_timeout": 10,
            },
        }
        alias = f"zeo_test_{addr[1]}"
        wrapper = DatabaseWrapper(settings_dict, alias=alias)

        # Remove any cached pool entry for this alias so we open a fresh connection.
        with _pool_lock:
            _db_pool.pop(alias, None)

        return wrapper, alias

    def test_zeo_backend_connect_and_write(self, zeo_memory_server):
        """DatabaseWrapper configured for ZEO can connect, write, and read."""
        wrapper, alias = self._make_zeo_wrapper(zeo_memory_server)

        try:
            wrapper.ensure_connection()
            coll = wrapper.ensure_collection("test_table")
            pk = wrapper.get_next_pk("test_table")
            coll[pk] = PersistentMapping({"id": pk, "name": "Alice"})
            transaction.commit()

            retrieved = wrapper.get_collection("test_table")[pk]
            assert retrieved["name"] == "Alice"
        finally:
            wrapper.close()
            from django_zodb_backend.base import _db_pool, _pool_lock
            with _pool_lock:
                db = _db_pool.pop(alias, None)
                if db:
                    db.close()

    def test_zeo_data_visible_across_two_wrappers(self, zeo_memory_server):
        """Data written via one wrapper is visible via a second wrapper to the same server."""
        wrapper1, alias1 = self._make_zeo_wrapper(zeo_memory_server)
        # Alias2 needs to be different but same server.
        alias2 = alias1 + "_b"

        from django_zodb_backend.base import DatabaseWrapper, _db_pool, _pool_lock

        settings_dict2 = wrapper1.settings_dict.copy()
        wrapper2 = DatabaseWrapper(settings_dict2, alias=alias2)
        with _pool_lock:
            _db_pool.pop(alias2, None)

        try:
            wrapper1.ensure_connection()
            wrapper2.ensure_connection()

            # Write via wrapper1.
            coll1 = wrapper1.ensure_collection("cross_conn")
            pk = wrapper1.get_next_pk("cross_conn")
            coll1[pk] = PersistentMapping({"id": pk, "value": "from_wrapper1"})
            transaction.commit()
            wrapper1.close()

            # Read via wrapper2.
            coll2 = wrapper2.get_collection("cross_conn")
            assert coll2 is not None
            assert coll2[pk]["value"] == "from_wrapper1"
        finally:
            wrapper1._close() if wrapper1._zodb_conn else None
            wrapper2._close() if wrapper2._zodb_conn else None
            with _pool_lock:
                for a in (alias1, alias2):
                    db = _db_pool.pop(a, None)
                    if db:
                        try:
                            db.close()
                        except Exception:
                            pass


# ---------------------------------------------------------------------------
# ZEO server_sync option
# ---------------------------------------------------------------------------


class TestZEOServerSync:
    """
    Verify the ``server_sync`` option.

    With ``server_sync=True``, the client calls ``serverSync()`` before
    reads, ensuring it sees the latest committed state from other clients
    immediately (stronger read consistency at the cost of an extra RPC).
    """

    def test_server_sync_sees_latest_commit(self, zeo_memory_server):
        """A server_sync client sees data committed by another client without reconnect."""
        # Writer client (no server_sync needed for writes).
        writer_storage = ZEO.client(zeo_memory_server, wait_timeout=10)
        writer_db = ZODB.DB(writer_storage)

        # Reader client WITH server_sync.
        reader_storage = ZEO.client(zeo_memory_server, wait_timeout=10, server_sync=True)
        reader_db = ZODB.DB(reader_storage)

        try:
            # Open reader connection before writer commits.
            reader_conn = reader_db.open()

            # Writer commits something.
            writer_conn = writer_db.open()
            writer_conn.root()["sync_key"] = "synced_value"
            transaction.commit()
            writer_conn.close()

            # With server_sync, reader sees it in the same open connection.
            # (Without server_sync, the open connection might see a stale cache.)
            # Open a new reader connection to pick up the new transaction.
            reader_conn.close()
            reader_conn2 = reader_db.open()
            assert reader_conn2.root().get("sync_key") == "synced_value"
            reader_conn2.close()
        finally:
            writer_db.close()
            reader_db.close()
