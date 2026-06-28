from django.db.backends.base.introspection import BaseDatabaseIntrospection, TableInfo


class DatabaseIntrospection(BaseDatabaseIntrospection):
    """
    Introspect the ZODB root to enumerate collections and their indexes.

    Django uses introspection primarily for:
    - ``inspectdb`` management command
    - Schema migration auto-detection
    - ``test_utils.CaptureQueriesContext``

    Because ZODB is schema-free, introspection returns the list of BTree
    containers in the root and their associated secondary index structures.
    """

    def get_table_list(self, cursor):
        """Return a list of TableInfo for all collections in the ZODB root."""
        root = self.connection.zodb_root
        return [
            TableInfo(name=key, type="t")
            for key in root
            # Skip internal metadata keys (prefixed with "__").
            if not key.startswith("__")
        ]

    def table_names(self, cursor=None, include_views=False):
        return [t.name for t in self.get_table_list(cursor)]

    def get_sequences(self, cursor, table_name, table_fields=()):
        return []

    def get_relations(self, cursor, table_name):
        return {}

    def get_constraints(self, cursor, table_name):
        """Return constraints/indexes defined on a collection."""
        root = self.connection.zodb_root
        meta_key = f"__meta_{table_name}"
        constraints = {}
        if meta_key in root:
            meta = root[meta_key]
            for idx_name, idx_info in meta.get("indexes", {}).items():
                constraints[idx_name] = {
                    "check": False,
                    "columns": idx_info.get("columns", []),
                    "definition": None,
                    "foreign_key": None,
                    "index": True,
                    "orders": [],
                    "primary_key": idx_info.get("primary_key", False),
                    "type": "idx",
                    "unique": idx_info.get("unique", False),
                    "options": {},
                }
        # Always include the implicit PK index.
        constraints["primary_key"] = {
            "check": False,
            "columns": ["id"],
            "definition": None,
            "foreign_key": None,
            "index": True,
            "orders": ["ASC"],
            "primary_key": True,
            "type": "idx",
            "unique": True,
            "options": {},
        }
        return constraints

    def get_table_description(self, cursor, table_name):
        return []
