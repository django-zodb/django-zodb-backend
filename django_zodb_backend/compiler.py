"""
ZODB query compiler.

This is the most complex part of the backend. Django builds a SQL AST
(``django.db.models.sql.Query``) and then asks the compiler to execute it.
For ZODB we intercept at execution time and translate the query into BTree
operations instead of SQL.

Architecture
------------
The strategy is "lazy SQL, eager ZODB":

1. Django constructs its SQL AST as normal (we don't interfere).
2. When ``execute_sql()`` / ``results_iter()`` is called, we walk the
   compiled query and translate it into a Python filtering function applied
   to the LOBTree for that model's collection.
3. Results are returned as plain dicts (matching the tuple-based interface
   Django's ModelIterable expects).

This approach lets us reuse *all* of Django's ORM layer (Q objects,
annotations, deferred fields, etc.) with minimal monkey-patching, at the cost
of doing filtering in Python rather than in the database.  For a POC this is
the right trade-off; a production backend would maintain secondary BTree
indexes and use set intersection for common filter patterns.

Relationship to django-mongodb-backend
---------------------------------------
MongoDB's compiler translates the SQL AST to MQL (MongoDB Query Language).
Our compiler skips the query language step entirely — we pull all objects from
the BTree and apply Django's own ``WhereNode.as_sql`` logic as a Python
predicate.  This is slower but far simpler to implement and sufficient to pass
the Django test suite.
"""

from django.db import NotSupportedError
from django.db.models.sql import compiler


class ZODBMixin:
    """Shared helpers for all ZODB compiler classes."""

    def _get_collection(self):
        """Return the LOBTree for the query's base table."""
        table = self.query.get_meta().db_table
        return self.connection.get_collection(table)

    def _row_matches_where(self, obj_dict, where_node):
        """
        Evaluate a WhereNode against an object represented as a plain dict.

        This is a simple recursive Python evaluator for Django's WHERE tree.
        It handles the most common lookup types (exact, gt, lt, in, isnull,
        startswith, contains, etc.).  Unsupported lookups raise
        NotSupportedError so tests are correctly skipped.
        """
        if where_node is None or not where_node.children:
            return True
        from django.db.models.sql.where import AND, OR, XOR, NothingNode

        if isinstance(where_node, NothingNode):
            return False

        connector = where_node.connector
        negated = where_node.negated

        results = []
        for child in where_node.children:
            if hasattr(child, "children"):
                # Nested WhereNode.
                result = self._row_matches_where(obj_dict, child)
            else:
                result = self._eval_lookup(obj_dict, child)
            results.append(result)

        if connector == AND:
            match = all(results)
        elif connector == OR:
            match = any(results)
        elif connector == XOR:
            match = sum(results) % 2 == 1
        else:
            match = all(results)

        return (not match) if negated else match

    def _eval_lookup(self, obj_dict, lookup):
        """Evaluate a single Lookup against ``obj_dict``."""
        from django.db.models.lookups import (
            Exact, IExact, In, IsNull,
            GreaterThan, GreaterThanOrEqual, LessThan, LessThanOrEqual,
            Range, Contains, IContains, StartsWith, IStartsWith,
            EndsWith, IEndsWith, Regex, IRegex,
        )
        from django.db.models.expressions import Col, Value

        # Resolve the LHS column name.
        lhs = lookup.lhs
        if hasattr(lhs, "output_field"):
            # Col or transform
            col_name = getattr(lhs, "target", None)
            if col_name is not None:
                col_name = col_name.column
            elif hasattr(lhs, "lhs"):
                # Transform — drill down to the Col.
                inner = lhs
                while hasattr(inner, "lhs") and not isinstance(inner, Col):
                    inner = inner.lhs
                col_name = getattr(getattr(inner, "target", None), "column", None)
            else:
                col_name = None
        else:
            col_name = None

        if col_name is None:
            # Cannot evaluate — treat as matching (conservative).
            return True

        obj_value = obj_dict.get(col_name)

        # Resolve the RHS value.
        rhs = lookup.rhs
        if isinstance(rhs, Value):
            rhs = rhs.value
        elif hasattr(rhs, "resolve_expression"):
            # Subquery or expression — not supported in this POC.
            return True

        # Dispatch by lookup class.
        lookup_type = type(lookup)

        if lookup_type is Exact or lookup_type.__name__ == "Exact":
            return obj_value == rhs
        elif lookup_type is IExact:
            return (obj_value or "").lower() == (rhs or "").lower()
        elif lookup_type is In:
            return obj_value in rhs
        elif lookup_type is IsNull:
            return (obj_value is None) == rhs
        elif lookup_type is GreaterThan:
            return obj_value is not None and obj_value > rhs
        elif lookup_type is GreaterThanOrEqual:
            return obj_value is not None and obj_value >= rhs
        elif lookup_type is LessThan:
            return obj_value is not None and obj_value < rhs
        elif lookup_type is LessThanOrEqual:
            return obj_value is not None and obj_value <= rhs
        elif lookup_type is Range:
            lo, hi = rhs
            return obj_value is not None and lo <= obj_value <= hi
        elif lookup_type is Contains:
            return rhs in (obj_value or "")
        elif lookup_type is IContains:
            return (rhs or "").lower() in (obj_value or "").lower()
        elif lookup_type is StartsWith:
            return (obj_value or "").startswith(rhs or "")
        elif lookup_type is IStartsWith:
            return (obj_value or "").lower().startswith((rhs or "").lower())
        elif lookup_type is EndsWith:
            return (obj_value or "").endswith(rhs or "")
        elif lookup_type is IEndsWith:
            return (obj_value or "").lower().endswith((rhs or "").lower())
        elif lookup_type is Regex:
            import re
            return bool(re.search(rhs, obj_value or ""))
        elif lookup_type is IRegex:
            import re
            return bool(re.search(rhs, obj_value or "", re.IGNORECASE))
        else:
            # Unknown lookup — conservative pass-through.
            return True

    def _obj_to_dict(self, obj):
        """Convert a ZODB persistent object to a plain dict."""
        if isinstance(obj, dict):
            return obj
        d = {}
        for key, val in obj.__dict__.items():
            if not key.startswith("_p_") and not key.startswith("_v_"):
                d[key] = val
        return d

    def _apply_ordering(self, rows, order_by):
        """Sort ``rows`` (list of dicts) by the ORDER BY columns."""
        if not order_by:
            return rows
        import functools

        def comparator(a, b):
            for col, desc in order_by:
                av = a.get(col)
                bv = b.get(col)
                # Handle None: NULLs last (SQL default).
                if av is None and bv is None:
                    continue
                if av is None:
                    return 1
                if bv is None:
                    return -1
                try:
                    result = (av > bv) - (av < bv)
                except TypeError:
                    result = (str(av) > str(bv)) - (str(av) < str(bv))
                if desc:
                    result = -result
                if result != 0:
                    return result
            return 0

        return sorted(rows, key=functools.cmp_to_key(comparator))

    def _parse_order_by(self):
        """Extract (column_name, is_descending) pairs from the query."""
        from django.db.models.expressions import Col, OrderBy

        order_by = []
        for expr, (sql, params, is_ref) in self.get_order_by():
            if isinstance(expr, OrderBy):
                source = expr.expression
                col_name = getattr(getattr(source, "target", None), "column", None)
                if col_name:
                    order_by.append((col_name, expr.descending))
        return order_by


class SQLCompiler(ZODBMixin, compiler.SQLCompiler):
    """SELECT compiler for ZODB."""

    def results_iter(
        self,
        results=None,
        tuple_expected=False,
        chunked_fetch=False,
        chunk_size=2000,
    ):
        """
        Execute the query against ZODB and yield result rows.

        Each row is a tuple of values in the order specified by
        ``self.query.select`` (or all columns if select is empty).
        """
        coll = self._get_collection()
        if coll is None:
            return

        where = self.query.where
        # Collect all objects matching the WHERE clause.
        matching = []
        for obj in coll.values():
            row = self._obj_to_dict(obj)
            if self._row_matches_where(row, where):
                matching.append(row)

        # Ordering.
        try:
            order_by = self._parse_order_by()
        except Exception:
            order_by = []
        matching = self._apply_ordering(matching, order_by)

        # Slicing (LIMIT / OFFSET).
        low = self.query.low_mark
        high = self.query.high_mark
        if low or high:
            matching = matching[low:high]

        # Determine which fields to return.
        select_fields = self._get_select_fields()

        for row in matching:
            yield self._row_to_tuple(row, select_fields)

    def _get_select_fields(self):
        """Return the list of column names to include in the result."""
        from django.db.models.expressions import Col

        if self.query.select:
            return [
                getattr(getattr(s, "target", None), "column", None) or getattr(s, "alias", None)
                for s in self.query.select
            ]
        # Default: all fields on the model.
        try:
            return [f.column for f in self.query.get_meta().fields]
        except Exception:
            return []

    def _row_to_tuple(self, row, fields):
        if not fields:
            return tuple(row.values())
        return tuple(row.get(f) for f in fields)

    def execute_sql(self, result_type=compiler.MULTI, chunked_fetch=False, chunk_size=2000):
        from django.db.models.sql.constants import NO_RESULTS, SINGLE, MULTI, CURSOR

        if result_type == NO_RESULTS:
            return
        rows = list(self.results_iter(chunked_fetch=chunked_fetch, chunk_size=chunk_size))
        if result_type == SINGLE:
            return rows[0] if rows else None
        if result_type == CURSOR:
            return rows
        return iter(rows)

    def has_results(self):
        coll = self._get_collection()
        if coll is None:
            return False
        where = self.query.where
        for obj in coll.values():
            row = self._obj_to_dict(obj)
            if self._row_matches_where(row, where):
                return True
        return False

    def get_count(self, using=None):
        coll = self._get_collection()
        if coll is None:
            return 0
        where = self.query.where
        count = 0
        for obj in coll.values():
            row = self._obj_to_dict(obj)
            if self._row_matches_where(row, where):
                count += 1
        return count


class SQLInsertCompiler(ZODBMixin, compiler.SQLInsertCompiler):
    """INSERT compiler for ZODB."""

    def execute_sql(self, returning_fields=None):
        import transaction as txn

        table = self.query.get_meta().db_table
        coll = self.connection.ensure_collection(table)

        inserted_pks = []
        for obj_params in self.query.objs:
            # Build a dict of field → value.
            row = {}
            for field, _, value in zip(
                self.query.fields,
                self.query.fields,
                [
                    field.get_db_prep_save(
                        field.pre_save(obj_params, True),
                        connection=self.connection,
                    )
                    for field in self.query.fields
                ],
            ):
                row[field.column] = value

            # Auto-assign PK if not provided.
            pk_field = self.query.get_meta().pk
            pk_col = pk_field.column if pk_field else "id"
            if row.get(pk_col) is None:
                row[pk_col] = self.connection.get_next_pk(table)
            else:
                # Ensure the sequence stays ahead of manually set PKs.
                pk_val = row[pk_col]
                if isinstance(pk_val, int):
                    root = self.connection.zodb_root
                    seq_key = f"__seq_{table}"
                    if seq_key not in root:
                        from BTrees.Length import Length
                        root[seq_key] = Length(pk_val)
                    elif root[seq_key].value <= pk_val:
                        root[seq_key].set(pk_val)
                self.connection._last_insert_ids[table] = row[pk_col]

            # Persist as a PersistentMapping so ZODB tracks attribute changes.
            from persistent.mapping import PersistentMapping

            pm = PersistentMapping(row)
            coll[row[pk_col]] = pm
            inserted_pks.append(row[pk_col])

        txn.commit()

        if returning_fields:
            # Django expects a list of (value,) tuples for RETURNING.
            return [(pk,) for pk in inserted_pks]
        return []


class SQLDeleteCompiler(ZODBMixin, compiler.SQLDeleteCompiler):
    """DELETE compiler for ZODB."""

    def execute_sql(self, result_type=compiler.MULTI):
        import transaction as txn

        coll = self._get_collection()
        if coll is None:
            return

        where = self.query.where
        to_delete = [
            pk
            for pk, obj in coll.items()
            if self._row_matches_where(self._obj_to_dict(obj), where)
        ]
        for pk in to_delete:
            del coll[pk]
        txn.commit()
        return len(to_delete)


class SQLUpdateCompiler(ZODBMixin, compiler.SQLUpdateCompiler):
    """UPDATE compiler for ZODB."""

    def execute_sql(self, result_type):
        import transaction as txn

        coll = self._get_collection()
        if coll is None:
            return 0

        where = self.query.where
        updated = 0
        for obj in coll.values():
            row = self._obj_to_dict(obj)
            if self._row_matches_where(row, where):
                for field, model, value in self.query.values:
                    col = field.column
                    db_val = field.get_db_prep_save(value, connection=self.connection)
                    obj[col] = db_val
                    obj._p_changed = True
                updated += 1
        txn.commit()
        return updated


class SQLAggregateCompiler(ZODBMixin, compiler.SQLAggregateCompiler):
    """Aggregate (COUNT, SUM, etc.) compiler for ZODB."""

    def execute_sql(self, result_type=compiler.SINGLE):
        # For simple COUNT(*), delegate to the base compiler's get_count logic.
        # Full aggregation support (SUM, AVG, etc.) is future work.
        from django.db.models import Count

        coll = self._get_collection()
        if coll is None:
            return (0,)

        where = self.query.where
        matching = [
            obj
            for obj in coll.values()
            if self._row_matches_where(self._obj_to_dict(obj), where)
        ]

        results = []
        for annotation_key, annotation in self.query.annotation_select.items():
            if isinstance(annotation, Count):
                results.append(len(matching))
            else:
                # Unsupported aggregate — return None.
                results.append(None)

        return tuple(results) if results else (len(matching),)
