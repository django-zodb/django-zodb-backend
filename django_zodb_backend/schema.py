"""
DatabaseSchemaEditor for ZODB.

Django's schema migration system calls these methods to create/drop tables,
add/remove columns, and manage indexes. In ZODB terms:

- "Create table"  → ensure an OOBTree container exists in the ZODB root
- "Drop table"    → delete the OOBTree container from the root
- "Add column"    → no-op (ZODB objects carry their own schema)
- "Remove column" → no-op
- "Create index"  → create a secondary BTree index structure
- "Drop index"    → remove the secondary BTree index structure

Because ZODB is schema-free, most operations are lightweight bookkeeping
rather than data transformation.
"""

import transaction
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


class DatabaseSchemaEditor(BaseDatabaseSchemaEditor):
    # ZODB doesn't execute DDL SQL — all operations are Python object mutations.

    def create_model(self, model):
        """Ensure the ZODB BTree for this model exists."""
        self.connection.ensure_btree(model._meta.db_table)

    def delete_model(self, model):
        """Drop the ZODB BTree for this model."""
        self.connection.drop_btree(model._meta.db_table)

    def add_field(self, model, field):
        """No-op: ZODB objects carry their own attributes."""
        pass

    def remove_field(self, model, field):
        """No-op: old attributes are simply ignored on read."""
        pass

    def alter_field(self, model, old_field, new_field, strict=False):
        """No-op: field type changes are transparent in Python objects."""
        pass

    def alter_db_table(self, model, old_db_table, new_db_table):
        """Rename a BTree by copying its BTree and deleting the old one."""
        root = self.connection.zodb_root
        if old_db_table in root and new_db_table not in root:
            root[new_db_table] = root[old_db_table]
            del root[old_db_table]
            transaction.commit()

    def alter_db_tablespace(self, model, old_db_tablespace, new_db_tablespace):
        pass  # No tablespace concept in ZODB.

    def add_index(self, model, index):
        """Create a secondary BTree index for the given field(s)."""
        self.connection.ensure_index(model._meta.db_table, index)

    def remove_index(self, model, index):
        """Drop the secondary BTree index."""
        self.connection.drop_index(model._meta.db_table, index)

    def add_constraint(self, model, constraint):
        pass  # Constraints not enforced at storage level.

    def remove_constraint(self, model, constraint):
        pass

    # Django calls these for deferred constraint checks — no-ops for ZODB.
    def deferred_sql(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            transaction.commit()
        else:
            transaction.abort()
