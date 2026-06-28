"""
DatabaseWrapper for ZODB.

This is the central class Django calls to interact with the database.  It owns
the ZODB DB / connection objects and exposes:

- ``get_collection(name)``  — returns the LOBTree for a model's table
- ``ensure_collection(name)`` — creates the LOBTree if absent
- ``drop_collection(name)``   — removes the LOBTree
- ``zodb_root`` property       — the ZODB root PersistentMapping
- ``get_last_insert_id(table)`` — returns the PK just inserted

Primary key strategy
--------------------
ZODB has no auto-increment built in.  We maintain a ``BTrees.Length.Length``
counter per collection stored at ``root["__seq_<table>"]``.  A Length object
supports atomic increment (it implements BTree conflict resolution), making
it safe under concurrent writes.

Relationship to django-mongodb-backend
---------------------------------------
MongoDB uses ObjectId (a 12-byte opaque identifier) as its PK, which required
forking Django's test suite to replace integer ``object_id`` FK fields with
``TextField``.  ZODB uses plain 64-bit integers — the default for Django's
``BigAutoField`` — so the test-suite changes we need are *much* smaller.
"""

import threading

import transaction
import ZODB
import ZODB.MappingStorage
from BTrees.LOBTree import LOBTree
from BTrees.Length import Length
from BTrees.OOBTree import OOBTree
from django.db import DEFAULT_DB_ALIAS
from django.db.backends.base.base import BaseDatabaseWrapper

from . import dbapi as Database
from .client import DatabaseClient
from .creation import DatabaseCreation
from .features import DatabaseFeatures
from .introspection import DatabaseIntrospection
from .operations import DatabaseOperations
from .schema import DatabaseSchemaEditor
from .validation import DatabaseValidation


class Cursor:
    """
    DB-API cursor stub.

    ZODB has no cursor-based query interface; this stub satisfies Django's
    internal machinery (e.g. schema migration, management commands) without
    actually executing SQL.
    """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def execute(self, query, args=None):
        from django.db import NotSupportedError

        raise NotSupportedError(f"ZODB does not support cursor.execute(): {query!r}")

    def executemany(self, query, args):
        from django.db import NotSupportedError

        raise NotSupportedError("ZODB does not support cursor.executemany().")

    def callproc(self, procname, params=None):
        from django.db import NotSupportedError

        raise NotSupportedError("ZODB does not support cursor.callproc().")


# Per-alias ZODB DB objects, shared across threads (ZODB connections are
# opened per-thread from the shared DB).
_db_pool: dict = {}
_pool_lock = threading.Lock()


class DatabaseWrapper(BaseDatabaseWrapper):
    """
    Django database backend for ZODB.

    Storage configuration is specified via ``DATABASES[alias]["OPTIONS"]``:

    .. code-block:: python

        DATABASES = {
            "default": {
                "ENGINE": "django_zodb_backend",
                "NAME": "mydb",
                "OPTIONS": {
                    # "memory"    — in-process MappingStorage (default / tests)
                    # "file"      — FileStorage; requires "PATH" in OPTIONS
                    # "zeo"       — ZEO ClientStorage; requires "HOST"/"PORT"
                    "storage": "memory",
                },
            }
        }
    """

    # Django uses data_types to build SQL DDL — we keep stubs so that field
    # validation and form widgets work without modification.
    data_types = {
        "AutoField": "integer",
        "BigAutoField": "bigint",
        "BinaryField": "blob",
        "BooleanField": "bool",
        "CharField": "varchar",
        "DateField": "date",
        "DateTimeField": "datetime",
        "DecimalField": "decimal",
        "DurationField": "bigint",
        "FileField": "varchar",
        "FilePathField": "varchar",
        "FloatField": "real",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "GenericIPAddressField": "varchar",
        "JSONField": "text",
        "NullBooleanField": "bool",
        "OneToOneField": "integer",
        "PositiveBigIntegerField": "bigint",
        "PositiveIntegerField": "integer",
        "PositiveSmallIntegerField": "smallint",
        "SlugField": "varchar",
        "SmallAutoField": "smallint",
        "SmallIntegerField": "smallint",
        "TextField": "text",
        "TimeField": "time",
        "UUIDField": "varchar",
    }

    # Standard SQL operators — needed so Django's WHERE clause builder doesn't
    # crash.  The compiler overrides actual query execution with ZODB logic.
    operators = {
        "exact": "= %s",
        "iexact": "= UPPER(%s)",
        "contains": "LIKE %s",
        "icontains": "LIKE UPPER(%s)",
        "regex": "~ %s",
        "iregex": "~* %s",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE %s",
        "endswith": "LIKE %s",
        "istartswith": "LIKE UPPER(%s)",
        "iendswith": "LIKE UPPER(%s)",
    }
    pattern_esc = "%%"
    pattern_ops = {
        "contains": "LIKE '%%' || {} || '%%'",
        "icontains": "LIKE '%%' || UPPER({}) || '%%'",
        "startswith": "LIKE {} || '%%'",
        "istartswith": "LIKE UPPER({}) || '%%'",
        "endswith": "LIKE '%%' || {}",
        "iendswith": "LIKE '%%' || UPPER({})",
    }

    display_name = "ZODB"
    vendor = "zodb"
    Database = Database
    SchemaEditorClass = DatabaseSchemaEditor
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations
    validation_class = DatabaseValidation

    # Track the last PK inserted per-table (used by operations.last_insert_id).
    _last_insert_ids: dict

    def __init__(self, settings_dict, alias=DEFAULT_DB_ALIAS):
        super().__init__(settings_dict, alias=alias)
        self._zodb_conn = None
        self._last_insert_ids = {}
        # Use in-memory storage by default (overridden in get_connection_params).

    # -------------------------------------------------------------------------
    # ZODB connection lifecycle
    # -------------------------------------------------------------------------

    def get_connection_params(self):
        opts = self.settings_dict.get("OPTIONS", {})
        return {
            "storage_type": opts.get("storage", "memory"),
            "name": self.settings_dict.get("NAME", "default"),
            "options": opts,
        }

    def get_new_connection(self, conn_params):
        """Return (or create) the shared ZODB DB object for this alias."""
        alias = self.alias
        with _pool_lock:
            if alias not in _db_pool:
                _db_pool[alias] = self._make_db(conn_params)
        return _db_pool[alias]

    def _make_db(self, conn_params):
        """Construct a ZODB.DB from connection params."""
        storage_type = conn_params["storage_type"]
        opts = conn_params["options"]

        if storage_type == "memory":
            storage = ZODB.MappingStorage.MappingStorage()
        elif storage_type == "file":
            from ZODB.FileStorage import FileStorage

            path = opts.get("PATH") or (conn_params["name"] + ".fs")
            storage = FileStorage(path)
        elif storage_type == "zeo":
            # ZEO ClientStorage — connects to a running ZEO server.
            # Supports both (host, port) TCP and Unix socket (path) addresses.
            #
            # OPTIONS keys:
            #   HOST          hostname or IP (default: localhost)
            #   PORT          TCP port (default: 8001)
            #   PATH          Unix socket path (overrides HOST/PORT when set)
            #   wait_timeout  seconds to wait for server (default: 30)
            #   read_only     open read-only connection (default: False)
            #   server_sync   call serverSync() before each read for stronger
            #                 consistency in multi-client scenarios (default: False)
            #
            # For production, run the ZEO server with:
            #   runzeo -a localhost:8001 -f /var/lib/myapp/data.fs
            #
            # For testing, use ZEO.server() to start an in-process server:
            #   addr, stop = ZEO.server(path="/tmp/test.fs")
            try:
                from ZEO import client as zeo_client
            except ImportError as exc:
                raise ImportError(
                    "ZEO storage requires the 'ZEO' package: pip install ZEO"
                ) from exc

            if unix_path := opts.get("PATH"):
                addr = unix_path
            else:
                host = opts.get("HOST", "localhost")
                port = int(opts.get("PORT", 8001))
                addr = (host, port)

            storage = zeo_client(
                addr,
                wait_timeout=int(opts.get("wait_timeout", 30)),
                read_only=bool(opts.get("read_only", False)),
                server_sync=bool(opts.get("server_sync", False)),
            )
        else:
            raise ValueError(f"Unknown ZODB storage type: {storage_type!r}")

        return ZODB.DB(storage)

    def init_connection_state(self):
        """Open a per-thread ZODB connection from the shared DB."""
        db = self.connection  # the ZODB.DB object from get_new_connection
        self._zodb_conn = db.open(transaction.manager)
        super().init_connection_state()

    @property
    def zodb_root(self):
        if self._zodb_conn is None:
            self.ensure_connection()
        return self._zodb_conn.root()

    def get_collection(self, name):
        """Return the LOBTree for a table, or None if it doesn't exist."""
        root = self.zodb_root
        return root.get(name)

    def ensure_collection(self, name):
        """Create the LOBTree for a table if it does not exist yet."""
        root = self.zodb_root
        if name not in root:
            root[name] = LOBTree()
            transaction.commit()
        return root[name]

    def drop_collection(self, name):
        root = self.zodb_root
        if name in root:
            del root[name]
        seq_key = f"__seq_{name}"
        if seq_key in root:
            del root[seq_key]
        transaction.commit()

    def ensure_index(self, table_name, index):
        """Create a secondary BTree index structure."""
        root = self.zodb_root
        meta_key = f"__meta_{table_name}"
        if meta_key not in root:
            from persistent.mapping import PersistentMapping

            root[meta_key] = PersistentMapping({"indexes": {}})
        idx_name = index.name
        columns = [f.column for f in index.fields]
        root[meta_key]["indexes"][idx_name] = {
            "columns": columns,
            "unique": getattr(index, "unique", False),
        }
        # Create the index BTree.
        idx_collection_key = f"__idx_{table_name}_{idx_name}"
        if idx_collection_key not in root:
            root[idx_collection_key] = OOBTree()
        transaction.commit()

    def drop_index(self, table_name, index):
        root = self.zodb_root
        meta_key = f"__meta_{table_name}"
        if meta_key in root:
            root[meta_key]["indexes"].pop(index.name, None)
        idx_collection_key = f"__idx_{table_name}_{index.name}"
        if idx_collection_key in root:
            del root[idx_collection_key]
        transaction.commit()

    # -------------------------------------------------------------------------
    # Auto-increment PK support
    # -------------------------------------------------------------------------

    def get_next_pk(self, table_name: str) -> int:
        """
        Return the next auto-increment PK for ``table_name``.

        Uses a ``BTrees.Length.Length`` object which implements conflict
        resolution so concurrent increments are safe under optimistic
        concurrency.
        """
        root = self.zodb_root
        seq_key = f"__seq_{table_name}"
        if seq_key not in root:
            root[seq_key] = Length(1)
        else:
            root[seq_key].change(1)
        pk = root[seq_key].value
        self._last_insert_ids[table_name] = pk
        return pk

    def get_last_insert_id(self, table_name: str) -> int:
        return self._last_insert_ids.get(table_name)

    # -------------------------------------------------------------------------
    # Transaction management
    # -------------------------------------------------------------------------

    def _commit(self):
        transaction.commit()

    def _rollback(self):
        transaction.abort()

    def _close(self):
        if self._zodb_conn is not None:
            # Must abort any open transaction before closing the connection;
            # ZODB raises ConnectionStateError otherwise.
            try:
                transaction.abort()
            except Exception:
                pass
            try:
                self._zodb_conn.close()
            except Exception:
                pass
            self._zodb_conn = None

    def set_autocommit(self, autocommit, force_begin_transaction_with_broken_autocommit=False):
        self.autocommit = autocommit

    def close(self):
        self.validate_thread_sharing()
        self._close()
        self.connection = None

    # -------------------------------------------------------------------------
    # Test support
    # -------------------------------------------------------------------------

    def switch_to_test_storage(self):
        """
        Replace the shared DB for this alias with a fresh in-memory instance.

        Called by DatabaseCreation._create_test_db().  All subsequent
        connections to this alias will use the new in-memory storage.
        """
        self.close()
        with _pool_lock:
            if self.alias in _db_pool:
                try:
                    _db_pool[self.alias].close()
                except Exception:
                    pass
            storage = ZODB.MappingStorage.MappingStorage()
            _db_pool[self.alias] = ZODB.DB(storage)
        self.ensure_connection()

    def close_test_storage(self):
        """Close and remove the in-memory test DB."""
        self.close()
        with _pool_lock:
            db = _db_pool.pop(self.alias, None)
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Cursor stub — Django requires this even though ZODB doesn't use cursors.
    # -------------------------------------------------------------------------

    def cursor(self):
        return Cursor()

    def chunked_cursor(self):
        return Cursor()

    def get_database_version(self):
        import ZODB

        return (int(ZODB.__spec__.origin.split("/")[-3].split(".")[0]),)
