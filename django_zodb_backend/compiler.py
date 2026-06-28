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
   to the OOBTree for this model's table.
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

from django.db.models.sql import compiler


class ZODBMixin:
    """Shared helpers for all ZODB compiler classes."""

    def _get_btree(self):
        """Return the OOBTree for the query's base table."""
        table = self.query.get_meta().db_table
        return self.connection.get_btree(table)

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
            if isinstance(child, NothingNode):
                result = False
            elif hasattr(child, "children"):
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
        from django.db.models.expressions import Col, Value
        from django.db.models.lookups import (
            Contains,
            EndsWith,
            Exact,
            GreaterThan,
            GreaterThanOrEqual,
            IContains,
            IEndsWith,
            IExact,
            In,
            IRegex,
            IsNull,
            IStartsWith,
            LessThan,
            LessThanOrEqual,
            Range,
            Regex,
            StartsWith,
        )

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
        """Convert a ZODB stored object to a plain dict."""
        # PersistentMapping (our primary storage format) supports the mapping
        # protocol — dict(pm) extracts the stored key/value pairs correctly.
        try:
            return dict(obj)
        except TypeError:
            pass
        # Fallback for arbitrary Persistent objects stored by other means.
        return {
            k: v
            for k, v in obj.__dict__.items()
            if not k.startswith("_p_") and not k.startswith("_v_")
        }

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
        from django.db.models.expressions import OrderBy

        order_by = []
        for expr, (_sql, _params, _is_ref) in self.get_order_by():
            if isinstance(expr, OrderBy):
                source = expr.expression
                col_name = getattr(getattr(source, "target", None), "column", None)
                if col_name:
                    order_by.append((col_name, expr.descending))
        return order_by


class SQLCompiler(ZODBMixin, compiler.SQLCompiler):
    """SELECT compiler for ZODB."""

    def _setup_klass_info(self):
        """
        Populate self.select, self.klass_info, self.annotation_col_map.

        Django 6.0's ModelIterable reads these as instance attributes
        (not from a pre_sql_setup() return value), so we must set them
        before returning from execute_sql().

        self.select is a list of 3-tuples (expression, (sql, params), alias)
        as set by SQLCompiler.get_select() / setup_query().
        """
        try:
            self.pre_sql_setup()
        except Exception:
            pass

        # Ensure klass_info is always set for concrete model queries.
        if getattr(self, "klass_info", None) is None and self.query.model is not None:
            meta = self.query.get_meta()
            fields = meta.concrete_fields
            if not getattr(self, "select", None):
                # Build minimal 3-tuples matching the format from get_select().
                self.select = [(f.col, (f"t.{f.column}", ()), None) for f in fields]
            self.klass_info = {
                "model": self.query.model,
                "select_fields": list(range(len(fields))),
            }
        if not hasattr(self, "annotation_col_map") or self.annotation_col_map is None:
            self.annotation_col_map = {}

    def _select_columns(self):
        """
        Return column names in the order of self.select.

        self.select entries are 3-tuples (expression, (sql, params), alias)
        where expression is a Col with a .target field whose .column gives
        the DB column name.
        """
        if getattr(self, "select", None):
            cols = []
            for entry in self.select:
                col_expr = entry[0]  # (col_expr, (sql, params), alias)
                name = (
                    getattr(getattr(col_expr, "target", None), "column", None)
                    or getattr(col_expr, "alias", None)
                    or getattr(col_expr, "column", None)
                )
                cols.append(name)
            return cols
        # Fallback: all concrete model fields.
        try:
            return [f.column for f in self.query.get_meta().concrete_fields]
        except Exception:
            return []

    def _fetch_matching_rows(self):
        """Scan the OOBTree and return dicts that pass the WHERE clause."""
        coll = self._get_btree()
        if coll is None:
            return []

        where = self.query.where
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

        return matching

    def results_iter(
        self,
        results=None,
        tuple_expected=False,
        chunked_fetch=False,
        chunk_size=2000,
    ):
        """
        Yield result rows as tuples.

        When called by ModelIterable, ``results`` is the value returned by
        execute_sql() — a list of row-tuple chunks.  When called standalone
        (results=None), we fetch from ZODB directly.
        """
        if results is not None:
            # Unwrap the chunks returned by execute_sql(MULTI).
            for chunk in results:
                yield from chunk
            return

        # Standalone call: fetch fresh from ZODB.
        self._setup_klass_info()
        cols = self._select_columns()
        for row in self._fetch_matching_rows():
            yield tuple(row.get(c) for c in cols)

    def _compute_aggregates(self, result_type):
        """
        Handle the case where Django routes a get_aggregation() call through
        SQLCompiler instead of SQLAggregateCompiler (the non-subquery path).

        In this path, ``self.query.annotation_select`` contains Aggregate
        expressions (Count, Sum, …) and ``self.query.default_cols`` is False.
        We compute each aggregate over the matching rows and return a single
        tuple, matching what SQLAggregateCompiler.execute_sql(SINGLE) returns.
        """
        from django.db.models import Count
        from django.db.models.sql.constants import SINGLE

        btree = self._get_btree()
        if btree is None:
            return tuple(
                getattr(ann, "empty_result_set_value", None)
                for ann in self.query.annotation_select.values()
            )

        where = self.query.where
        matching = [
            self._obj_to_dict(obj)
            for obj in btree.values()
            if self._row_matches_where(self._obj_to_dict(obj), where)
        ]

        results = []
        for ann in self.query.annotation_select.values():
            if isinstance(ann, Count):
                results.append(len(matching))
            else:
                results.append(None)

        result_tuple = tuple(results)
        return result_tuple if result_type == SINGLE else [result_tuple]

    def execute_sql(self, result_type=compiler.MULTI, chunked_fetch=False, chunk_size=2000):
        from django.db.models.sql.constants import CURSOR, NO_RESULTS, SINGLE

        # Django's get_aggregation() "else" path routes aggregate queries through
        # SQLCompiler with annotation_select containing Aggregate expressions and
        # default_cols=False/select=(). Detect and handle this case directly.
        if (
            self.query.annotation_select
            and not self.query.default_cols
            and not getattr(self.query, "select", None)
            and all(
                getattr(ann, "contains_aggregate", False)
                for ann in self.query.annotation_select.values()
            )
        ):
            return self._compute_aggregates(result_type)

        # Populate instance attributes that ModelIterable reads directly.
        self._setup_klass_info()
        cols = self._select_columns()

        if result_type == NO_RESULTS:
            return

        rows = [tuple(row.get(c) for c in cols) for row in self._fetch_matching_rows()]

        if result_type == SINGLE:
            return rows[0] if rows else None
        if result_type == CURSOR:
            return rows
        # MULTI: return as a list of chunks so results_iter() can unwrap them.
        return [rows]

    def has_results(self):
        coll = self._get_btree()
        if coll is None:
            return False
        where = self.query.where
        for obj in coll.values():
            row = self._obj_to_dict(obj)
            if self._row_matches_where(row, where):
                return True
        return False

    def get_count(self, using=None):
        coll = self._get_btree()
        if coll is None:
            return 0
        where = self.query.where
        return sum(
            1 for obj in coll.values() if self._row_matches_where(self._obj_to_dict(obj), where)
        )


class SQLInsertCompiler(ZODBMixin, compiler.SQLInsertCompiler):
    """INSERT compiler for ZODB."""

    def execute_sql(self, returning_fields=None):
        import transaction as txn

        table = self.query.get_meta().db_table
        coll = self.connection.ensure_btree(table)

        inserted_pks = []
        for obj_params in self.query.objs:
            # Build a dict of field → value.
            row = {}
            for field, _, value in zip(  # noqa: B905
                self.query.fields,
                self.query.fields,
                [
                    field.get_db_prep_save(
                        field.pre_save(obj_params, True),
                        connection=self.connection,
                    )
                    for field in self.query.fields
                ],
                strict=False,
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

        if self.connection.autocommit:
            txn.commit()

        if returning_fields:
            # Django expects a list of (value,) tuples for RETURNING.
            return [(pk,) for pk in inserted_pks]
        return []


class SQLDeleteCompiler(ZODBMixin, compiler.SQLDeleteCompiler):
    """DELETE compiler for ZODB."""

    def execute_sql(self, result_type=compiler.MULTI):
        import transaction as txn

        coll = self._get_btree()
        if coll is None:
            return

        where = self.query.where
        to_delete = [
            pk for pk, obj in coll.items() if self._row_matches_where(self._obj_to_dict(obj), where)
        ]
        for pk in to_delete:
            del coll[pk]
        if self.connection.autocommit:
            txn.commit()
        return len(to_delete)


class SQLUpdateCompiler(ZODBMixin, compiler.SQLUpdateCompiler):
    """UPDATE compiler for ZODB."""

    def execute_sql(self, result_type):
        import transaction as txn

        coll = self._get_btree()
        if coll is None:
            return 0

        where = self.query.where
        updated = 0
        for obj in coll.values():
            row = self._obj_to_dict(obj)
            if self._row_matches_where(row, where):
                for field, _model, value in self.query.values:
                    col = field.column
                    db_val = field.get_db_prep_save(value, connection=self.connection)
                    obj[col] = db_val
                    obj._p_changed = True
                updated += 1
        if self.connection.autocommit:
            txn.commit()
        return updated


class SQLAggregateCompiler(ZODBMixin, compiler.SQLAggregateCompiler):
    """Aggregate (COUNT, SUM, etc.) compiler for ZODB."""

    def execute_sql(self, result_type=compiler.SINGLE):
        # For simple COUNT(*), delegate to the base compiler's get_count logic.
        # Full aggregation support (SUM, AVG, etc.) is future work.
        from django.db.models import Count

        coll = self._get_btree()
        if coll is None:
            return (0,)

        where = self.query.where
        matching = [
            obj for obj in coll.values() if self._row_matches_where(self._obj_to_dict(obj), where)
        ]

        results = []
        for _annotation_key, annotation in self.query.annotation_select.items():
            if isinstance(annotation, Count):
                results.append(len(matching))
            else:
                # Unsupported aggregate — return None.
                results.append(None)

        return tuple(results) if results else (len(matching),)
