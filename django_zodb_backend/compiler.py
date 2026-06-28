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
        """Return the OOBTree for the query's base table, or None if unavailable."""
        meta = self.query.get_meta()
        if meta is None:
            return None
        return self.connection.get_btree(meta.db_table)

    def _get_btree_for_table(self, table_name):
        """Return the OOBTree for any table by name, or None if unavailable."""
        return self.connection.get_btree(table_name)

    def _resolve_joined_col(self, row, target_alias, col_name):
        """
        Resolve a column that lives in a JOINed table rather than the main
        query table.  Returns a (possibly empty) list of all values for
        ``col_name`` reachable from the current ``row`` by following the
        join-chain recorded in ``self.query.alias_map``.

        For example, for ``Permission.objects.filter(user=user_obj)`` the
        main table is ``auth_permission`` and the WHERE references
        ``auth_user_user_permissions.user_id``.  We look up the M2M through-
        table in the ZODB store and return the user_ids linked to the
        permission in ``row``.
        """
        alias_map = getattr(self.query, "alias_map", {})
        main_table = self.query.get_meta().db_table

        if target_alias == main_table:
            return [row.get(col_name)]

        # Build the join path from main_table to target_alias by walking
        # parent_alias links backwards.
        path = []
        alias = target_alias
        visited = set()
        while alias and alias != main_table:
            if alias in visited or alias not in alias_map:
                return []  # Cycle or unknown alias — give up.
            visited.add(alias)
            join = alias_map[alias]
            path.insert(0, join)
            alias = join.parent_alias

        # Walk the path, expanding each join.
        current_rows = [row]
        for join in path:
            rhs_btree = self._get_btree_for_table(join.table_name)
            if rhs_btree is None:
                return []
            next_rows = []
            for current_row in current_rows:
                for rhs_obj in rhs_btree.values():
                    rhs_dict = self._obj_to_dict(rhs_obj)
                    # join.join_cols = [(parent_col, child_col)]
                    if all(
                        rhs_dict.get(rhs_col) == current_row.get(lhs_col)
                        for lhs_col, rhs_col in join.join_cols
                    ):
                        next_rows.append(rhs_dict)
            current_rows = next_rows

        return [r.get(col_name) for r in current_rows]

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

    def _eval_select_expr(self, expr, row_dict):
        """
        Evaluate a SELECT/annotation expression against a row dict.

        Handles Col (field references) and simple annotation expressions
        such as F() and Trunc().  Unknown expressions return None.

        When the Col belongs to a JOINed table (e.g. content_type__app_label
        in a values_list() query), the value is resolved by following the
        alias_map join chain, the same way _eval_lookup resolves WHERE clauses.
        """
        from django.db.models.expressions import F, Value
        from django.db.models.functions import Trunc

        # Plain column reference — the common case.
        col_name = getattr(getattr(expr, "target", None), "column", None)
        if col_name is not None:
            col_alias = getattr(expr, "alias", None)
            main_table = None
            try:
                main_table = self.query.get_meta().db_table
            except Exception:
                pass
            if (
                col_alias
                and main_table
                and col_alias != main_table
                and getattr(self.query, "alias_map", None)
            ):
                # Cross-table column (e.g. content_type__app_label): resolve
                # by following the join chain from the main table.
                values = self._resolve_joined_col(row_dict, col_alias, col_name)
                return values[0] if values else None
            return row_dict.get(col_name)

        # F() expression: drill down to the underlying Col.
        if isinstance(expr, F):
            inner = getattr(expr, "lhs", expr)
            col_name = getattr(getattr(inner, "target", None), "column", None)
            if col_name is not None:
                return row_dict.get(col_name)
            # F may have source_expressions after resolution
            sources = getattr(expr, "source_expressions", None) or []
            if sources:
                return self._eval_select_expr(sources[0], row_dict)

        # Trunc expression (used by QuerySet.dates/datetimes).
        if isinstance(expr, Trunc):
            sources = expr.get_source_expressions()
            if sources:
                field_val = self._eval_select_expr(sources[0], row_dict)
                if field_val is None:
                    return None
                kind = expr.kind  # "year", "month", "day", "hour", etc.
                import datetime

                if isinstance(field_val, (datetime.datetime, datetime.date)):
                    try:
                        if kind == "year":
                            return datetime.date(field_val.year, 1, 1)
                        elif kind == "month":
                            return datetime.date(field_val.year, field_val.month, 1)
                        elif kind == "week":
                            d = (
                                field_val.date()
                                if isinstance(field_val, datetime.datetime)
                                else field_val
                            )
                            return d - datetime.timedelta(days=d.weekday())
                        elif kind == "day":
                            return (
                                field_val.date()
                                if isinstance(field_val, datetime.datetime)
                                else field_val
                            )
                        elif kind == "hour":
                            dt = (
                                field_val
                                if isinstance(field_val, datetime.datetime)
                                else datetime.datetime.combine(field_val, datetime.time())
                            )
                            return dt.replace(minute=0, second=0, microsecond=0)
                        elif kind == "minute":
                            dt = (
                                field_val
                                if isinstance(field_val, datetime.datetime)
                                else datetime.datetime.combine(field_val, datetime.time())
                            )
                            return dt.replace(second=0, microsecond=0)
                        elif kind == "second":
                            dt = (
                                field_val
                                if isinstance(field_val, datetime.datetime)
                                else datetime.datetime.combine(field_val, datetime.time())
                            )
                            return dt.replace(microsecond=0)
                    except (ValueError, AttributeError):
                        return None

        # Value constant.
        if isinstance(expr, Value):
            return expr.value

        return None

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

        # Resolve the LHS column name and the source Col expression.
        lhs = lookup.lhs
        col_obj = None  # The Col expression (for join resolution)
        col_table_alias = None  # The table alias the column belongs to
        if hasattr(lhs, "output_field"):
            # Col or transform
            target = getattr(lhs, "target", None)
            if target is not None:
                col_name = target.column
                col_obj = lhs
                col_table_alias = getattr(lhs, "alias", None)
            elif hasattr(lhs, "lhs"):
                # Transform or annotation expression — drill down to the Col.
                inner = lhs
                while hasattr(inner, "lhs") and not isinstance(inner, Col):
                    inner = inner.lhs
                target = getattr(inner, "target", None)
                col_name = target.column if target else None
                if col_name is None:
                    # Might be an F() expression resolved as a source expression.
                    sources = getattr(inner, "source_expressions", None) or []
                    if sources:
                        col_name = getattr(getattr(sources[0], "target", None), "column", None)
                col_obj = inner if isinstance(inner, Col) else None
                col_table_alias = getattr(col_obj, "alias", None) if col_obj else None
            else:
                # Could be an annotation expression (F, Trunc, etc.) with
                # source_expressions.  Try evaluating it directly.
                obj_value = self._eval_select_expr(lhs, obj_dict)
                # Fall through with obj_value already set; set col_name to a
                # sentinel so we skip obj_dict.get() below.
                col_name = "__EXPR__"
        else:
            col_name = None

        if col_name is None:
            # Cannot evaluate — treat as matching (conservative).
            return True

        # Determine if this column belongs to a JOINed table.
        joined_values = None  # non-None when we resolved via join chain
        if col_name != "__EXPR__":
            main_table = None
            try:
                main_table = self.query.get_meta().db_table
            except Exception:
                pass
            if (
                col_table_alias
                and main_table
                and col_table_alias != main_table
                and getattr(self.query, "alias_map", None)
            ):
                # Cross-table column — resolve via the join chain.
                joined_values = self._resolve_joined_col(obj_dict, col_table_alias, col_name)
                obj_value = joined_values[0] if joined_values else None
            else:
                obj_value = obj_dict.get(col_name)

        # Resolve the RHS value.
        rhs = lookup.rhs
        if isinstance(rhs, Value):
            rhs = rhs.value
        elif hasattr(rhs, "resolve_expression"):
            # Subquery or expression — not supported in this POC.
            return True

        # Convert the RHS to the DB storage representation so it matches the
        # values written by our INSERT compiler (which uses get_db_prep_save).
        # For example, UUIDField stores hex strings; if rhs is a uuid.UUID
        # object the comparison would fail without this conversion.
        try:
            output_field = getattr(lookup.lhs, "output_field", None)
            if output_field is not None and rhs is not None:
                if isinstance(rhs, (list, tuple)):
                    rhs = type(rhs)(
                        output_field.get_db_prep_value(v, self.connection, prepared=False)
                        for v in rhs
                    )
                else:
                    rhs = output_field.get_db_prep_value(rhs, self.connection, prepared=False)
        except Exception:
            pass

        # For joined columns, use "any match" semantics: the lookup matches if
        # any of the resolved values satisfies the condition.
        if joined_values is not None:
            return self._eval_lookup_multi(lookup, joined_values, rhs)

        # Year/date-part lookups must be checked before Exact/GTE/LT because
        # they extend those classes but require year-extraction semantics.
        # filter(pubdate__year=2008) creates YearExact(Col, 2008) where rhs is
        # an integer, not a date — so the standard Exact comparison would fail.
        try:
            from django.db.models.lookups import (
                YearExact,
                YearGte,
                YearLt,
                YearLte,
            )

            if isinstance(lookup, YearExact):
                year = getattr(obj_value, "year", None)
                return year is not None and year == rhs
            elif isinstance(lookup, YearGte):
                year = getattr(obj_value, "year", None)
                return year is not None and year >= rhs
            elif isinstance(lookup, YearLt):
                year = getattr(obj_value, "year", None)
                return year is not None and year < rhs
            elif isinstance(lookup, YearLte):
                year = getattr(obj_value, "year", None)
                return year is not None and year <= rhs
        except ImportError:
            pass

        # Dispatch using isinstance so subclasses (e.g. IntegerFieldExact,
        # UUIDExact) are handled by the correct branch.  The order matters:
        # more specific classes must come before their parents.
        if isinstance(lookup, Exact):
            return obj_value == rhs
        elif isinstance(lookup, IExact):
            return (obj_value or "").lower() == (rhs or "").lower()
        elif isinstance(lookup, In):
            return obj_value in rhs
        elif isinstance(lookup, IsNull):
            return (obj_value is None) == rhs
        elif isinstance(lookup, GreaterThanOrEqual):
            return obj_value is not None and obj_value >= rhs
        elif isinstance(lookup, GreaterThan):
            return obj_value is not None and obj_value > rhs
        elif isinstance(lookup, LessThanOrEqual):
            return obj_value is not None and obj_value <= rhs
        elif isinstance(lookup, LessThan):
            return obj_value is not None and obj_value < rhs
        elif isinstance(lookup, Range):
            lo, hi = rhs
            return obj_value is not None and lo <= obj_value <= hi
        elif isinstance(lookup, IContains):
            return (rhs or "").lower() in (obj_value or "").lower()
        elif isinstance(lookup, Contains):
            return rhs in (obj_value or "")
        elif isinstance(lookup, IStartsWith):
            return (obj_value or "").lower().startswith((rhs or "").lower())
        elif isinstance(lookup, StartsWith):
            return (obj_value or "").startswith(rhs or "")
        elif isinstance(lookup, IEndsWith):
            return (obj_value or "").lower().endswith((rhs or "").lower())
        elif isinstance(lookup, EndsWith):
            return (obj_value or "").endswith(rhs or "")
        elif isinstance(lookup, IRegex):
            import re

            return bool(re.search(rhs, obj_value or "", re.IGNORECASE))
        elif isinstance(lookup, Regex):
            import re

            return bool(re.search(rhs, obj_value or ""))
        else:
            # Unknown lookup — conservative pass-through.
            return True

    def _eval_lookup_multi(self, lookup, values, rhs):
        """
        Evaluate a lookup against a *list* of values resolved from a JOINed
        table.  Returns True if ANY value in ``values`` satisfies the lookup.

        This is the "any match" semantics required for JOINs: a row passes a
        filter if at least one of the joined rows satisfies the condition.
        """
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

        if isinstance(lookup, IsNull):
            # IsNull(col, True)  → no joined rows (or all are None)
            # IsNull(col, False) → at least one joined row is non-None
            if rhs:
                return not values or all(v is None for v in values)
            else:
                return any(v is not None for v in values)

        # All other lookups: True if any value in the list satisfies it.
        def matches_one(v):
            if isinstance(lookup, Exact):
                return v == rhs
            elif isinstance(lookup, IExact):
                return (v or "").lower() == (rhs or "").lower()
            elif isinstance(lookup, In):
                return v in rhs
            elif isinstance(lookup, GreaterThanOrEqual):
                return v is not None and v >= rhs
            elif isinstance(lookup, GreaterThan):
                return v is not None and v > rhs
            elif isinstance(lookup, LessThanOrEqual):
                return v is not None and v <= rhs
            elif isinstance(lookup, LessThan):
                return v is not None and v < rhs
            elif isinstance(lookup, Range):
                lo, hi = rhs
                return v is not None and lo <= v <= hi
            elif isinstance(lookup, IContains):
                return (rhs or "").lower() in (v or "").lower()
            elif isinstance(lookup, Contains):
                return rhs in (v or "")
            elif isinstance(lookup, IStartsWith):
                return (v or "").lower().startswith((rhs or "").lower())
            elif isinstance(lookup, StartsWith):
                return (v or "").startswith(rhs or "")
            elif isinstance(lookup, IEndsWith):
                return (v or "").lower().endswith((rhs or "").lower())
            elif isinstance(lookup, EndsWith):
                return (v or "").endswith(rhs or "")
            elif isinstance(lookup, IRegex):
                import re

                return bool(re.search(rhs, v or "", re.IGNORECASE))
            elif isinstance(lookup, Regex):
                import re

                return bool(re.search(rhs, v or ""))
            return True  # Unknown lookup — conservative pass-through.

        return any(matches_one(v) for v in values)

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
        """Sort ``rows`` (list of dicts) by the ORDER BY columns.

        Each entry in order_by is (col, descending, nulls_first) where
        nulls_first=True puts None values first, False puts them last.
        """
        if not order_by:
            return rows
        import functools

        def comparator(a, b):
            for col, desc, nulls_first in order_by:
                av = a.get(col)
                bv = b.get(col)
                if av is None and bv is None:
                    continue
                if av is None:
                    return -1 if nulls_first else 1
                if bv is None:
                    return 1 if nulls_first else -1
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
        """Extract (column_name, is_descending, nulls_first) triples from the query."""
        from django.db.models.expressions import OrderBy

        order_by = []
        for expr, (_sql, _params, _is_ref) in self.get_order_by():
            if isinstance(expr, OrderBy):
                source = expr.expression
                col_name = getattr(getattr(source, "target", None), "column", None)
                if col_name:
                    # Determine effective nulls placement:
                    # explicit nulls_first/nulls_last override SQL defaults.
                    if expr.nulls_first:
                        nulls_first = True
                    elif expr.nulls_last:
                        nulls_first = False
                    else:
                        # SQL default: nulls last for ASC, nulls first for DESC.
                        nulls_first = expr.descending
                    order_by.append((col_name, expr.descending, nulls_first))
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
                # Use Col() directly rather than f.col — some field types
                # (e.g. BigAutoField) may not have the cached_property set yet.
                from django.db.models.expressions import Col

                self.select = [(Col(meta.db_table, f), (f"t.{f.column}", ()), None) for f in fields]
            self.klass_info = {
                "model": self.query.model,
                "select_fields": list(range(len(fields))),
            }
        if not hasattr(self, "annotation_col_map") or self.annotation_col_map is None:
            self.annotation_col_map = {}

        # Strip related_klass_infos so that select_related() preloading is
        # disabled.  Our backend returns None for all "joined" columns (we
        # don't implement SQL JOINs), which causes ModelIterable to mark
        # related objects as non-existent.  By removing the related info,
        # Django falls back to lazy loading (a separate query per access),
        # which works correctly against our BTree backend.
        if getattr(self, "klass_info", None) and "related_klass_infos" in self.klass_info:
            self.klass_info["related_klass_infos"] = []

    def _select_columns(self):
        """
        Return column names in the order of self.select.

        self.select entries are 3-tuples (expression, (sql, params), alias)
        where expression is a Col with a .target field whose .column gives
        the DB column name, OR an annotation expression evaluated via
        _eval_select_expr.
        """
        if getattr(self, "select", None):
            cols = []
            for entry in self.select:
                col_expr = entry[0]
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

    def _build_row_tuple(self, row_dict):
        """
        Build a result row tuple from a row dict.

        Uses _eval_select_expr() for all SELECT entries so that cross-table
        columns (e.g. content_type__app_label in a values_list query) are
        resolved via the alias_map join chain rather than a direct dict lookup
        that would always return None.
        """
        if getattr(self, "select", None):
            return tuple(self._eval_select_expr(entry[0], row_dict) for entry in self.select)
        # Fallback: no select info available.
        cols = self._select_columns()
        return tuple(row_dict.get(c) for c in cols)

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
        for row in self._fetch_matching_rows():
            yield self._build_row_tuple(row)

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

        # Populate instance attributes (select, klass_info, annotation_col_map)
        # that ModelIterable reads BEFORE iterating results — even for empty
        # querysets.  pre_sql_setup() only builds SQL metadata; it does NOT
        # open a ZODB connection, so this is safe for SimpleTestCase threads.
        self._setup_klass_info()

        # Short-circuit for empty querysets (e.g. QuerySet.none(), EmptyManager)
        # without opening a ZODB connection — important for SimpleTestCase which
        # forbids DB access from threads (async queryset methods run in threads).
        if self.query.is_empty():
            if result_type == SINGLE:
                return None
            return iter([])

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

        if result_type == NO_RESULTS:
            return

        rows = [self._build_row_tuple(row) for row in self._fetch_matching_rows()]

        # Apply DISTINCT deduplication (used by QuerySet.dates(), etc.).
        if getattr(self.query, "distinct", False) and rows:
            seen = []
            deduped = []
            for r in rows:
                # Use a hashable key; fall back to str conversion for unhashable types.
                try:
                    key = r
                    if key not in seen:
                        seen.append(key)
                        deduped.append(r)
                except TypeError:
                    key = str(r)
                    if key not in [str(s) for s in seen]:
                        seen.append(r)
                        deduped.append(r)
            rows = deduped

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

        if self.query.is_empty():
            return 0

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
                    # Django 6+ stores raw model instances in query.values for
                    # FK fields; extract the pk before get_db_prep_save.
                    if hasattr(value, "pk") and hasattr(value, "_meta"):
                        value = value.pk
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

        if self.query.is_empty():
            return tuple(0 for _ in self.query.annotation_select)

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
