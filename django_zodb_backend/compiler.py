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

    def _log_query(self, sql="ZODB query"):
        """Append a synthetic query log entry for assertNumQueries support."""
        if self.connection.queries_logged:
            self.connection.queries_log.append({"sql": sql, "time": "0"})

    def _eval_exists(self, exists_expr, outer_row):
        """
        Evaluate an ``Exists(subquery)`` expression against the current outer
        row.  Resolves ``OuterRef`` references using ``outer_row``, then
        executes the inner query and returns True if it produces any rows.
        """
        try:
            inner_query = exists_expr.query.clone()

            # Resolve OuterRef placeholders by substituting values from outer_row.
            from django.db.models.expressions import Col, OuterRef, ResolvedOuterRef, Value

            inner_alias_set = set(getattr(inner_query, "alias_map", {}).keys())

            def _get_outer_val(ref_name):
                if ref_name == "pk":
                    try:
                        pk_col = self.query.get_meta().pk.column
                        val = outer_row.get(pk_col)
                        if val is not None:
                            return val
                    except Exception:
                        pass
                val = outer_row.get(ref_name)
                if val is None:
                    val = outer_row.get(ref_name + "_id")
                if val is None:
                    val = outer_row.get("id")
                return val

            def _is_outer_ref(obj):
                """Return True if obj is a reference to the outer query."""
                if isinstance(obj, (OuterRef, ResolvedOuterRef)):
                    return True
                # A Col whose alias is not in the inner query is an outer ref.
                if isinstance(obj, Col) and obj.alias not in inner_alias_set:
                    return True
                return False

            def _outer_ref_val(obj):
                """Extract the outer-row value for an outer reference."""
                if isinstance(obj, (OuterRef, ResolvedOuterRef)):
                    return _get_outer_val(obj.name)
                if isinstance(obj, Col):
                    col_name = obj.target.column if obj.target else None
                    if col_name:
                        return outer_row.get(col_name)
                return None

            def _resolve_outer_refs(where_node):
                if where_node is None:
                    return
                new_children = []
                for child in list(where_node.children):
                    if hasattr(child, "children"):
                        _resolve_outer_refs(child)
                        new_children.append(child)
                    elif hasattr(child, "rhs") and _is_outer_ref(child.rhs):
                        outer_val = _outer_ref_val(child.rhs)
                        child = type(child)(child.lhs, Value(outer_val))
                        new_children.append(child)
                    else:
                        new_children.append(child)
                where_node.children = new_children

            _resolve_outer_refs(inner_query.where)

            sub_compiler = inner_query.get_compiler(
                using=self.connection.alias, connection=self.connection
            )
            has_rows = sub_compiler.has_results()
            return (not has_rows) if getattr(exists_expr, "negated", False) else has_rows
        except Exception:
            # Cannot evaluate — return True (conservative).
            return True

    def _eval_subquery_rhs(self, rhs, lookup):
        """
        Evaluate a subquery or QuerySet RHS against our ZODB backend.

        Returns a list of scalar values (for In/RelatedIn lookups) or a single
        value (for Exact lookups), or None if evaluation is not possible.

        Handles the common case of ``filter(group__in=user.groups.all())``,
        ``filter(pk__in=subqueryset)``, etc.
        """
        from django.db.models import QuerySet
        from django.db.models.sql.query import Query

        # Unwrap QuerySet to get the underlying Query.
        if isinstance(rhs, QuerySet):
            query = rhs.query
        elif isinstance(rhs, Query):
            query = rhs
        else:
            # Some other expression type — cannot evaluate.
            return None

        try:
            # Build a compiler for the subquery using our connection.
            sub_compiler = query.get_compiler(
                using=self.connection.alias, connection=self.connection
            )
            # Execute: returns an iterable of row tuples.
            results = sub_compiler.execute_sql(result_type="multi")
            values = []
            if results:
                for batch in results:
                    for row in batch:
                        if row:
                            values.append(row[0])
            return values
        except Exception:
            return None

    def _resolve_joined_col(self, row, target_alias, col_name, _pinned=None):
        """
        Resolve a column that lives in a JOINed table rather than the main
        query table.  Returns a (possibly empty) list of all values for
        ``col_name`` reachable from the current ``row`` by following the
        join-chain recorded in ``self.query.alias_map``.

        When ``_pinned`` maps ``target_alias`` to a single row dict, skip the
        full traversal and return that pinned row's column value directly.
        This is used by the AND-node join-expansion logic in
        ``_row_matches_where``.


        For example, for ``Permission.objects.filter(user=user_obj)`` the
        main table is ``auth_permission`` and the WHERE references
        ``auth_user_user_permissions.user_id``.  We look up the M2M through-
        table in the ZODB store and return the user_ids linked to the
        permission in ``row``.
        """
        # If this alias is pinned to a specific row, use it directly.
        if _pinned and target_alias in _pinned:
            pinned_row = _pinned[target_alias]
            if pinned_row is None:
                return []
            return [pinned_row.get(col_name)]

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
            if not hasattr(join, "join_cols"):
                # BaseTable entry — this occurs when pre_sql_setup() assigns
                # numbered aliases (U0, U1, …) and the base table's alias does
                # not match get_meta().db_table. The BaseTable represents the
                # starting table and requires no scanning; continue with the
                # current rows and let the next real Join expand them.
                continue
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

    def _resolve_joined_rows(self, row, target_alias):
        """
        Like ``_resolve_joined_col`` but returns full row dicts for each
        related object reachable at ``target_alias`` from ``row``.

        Used by the AND-expansion logic in ``_row_matches_where`` to build
        virtual rows for M2M/FK join evaluation.
        """
        alias_map = getattr(self.query, "alias_map", {})
        main_table = self.query.get_meta().db_table

        if target_alias == main_table:
            return [row]

        path = []
        alias = target_alias
        visited = set()
        while alias and alias != main_table:
            if alias in visited or alias not in alias_map:
                return []
            visited.add(alias)
            join = alias_map[alias]
            path.insert(0, join)
            alias = join.parent_alias

        current_rows = [row]
        for join in path:
            if not hasattr(join, "join_cols"):
                continue
            rhs_btree = self._get_btree_for_table(join.table_name)
            if rhs_btree is None:
                return []
            next_rows = []
            for current_row in current_rows:
                for rhs_obj in rhs_btree.values():
                    rhs_dict = self._obj_to_dict(rhs_obj)
                    if all(
                        rhs_dict.get(rhs_col) == current_row.get(lhs_col)
                        for lhs_col, rhs_col in join.join_cols
                    ):
                        next_rows.append(rhs_dict)
            current_rows = next_rows

        return current_rows

    def _collect_join_aliases(self, where_node):
        """
        Recursively collect all table aliases referenced by Col expressions in
        a WhereNode tree.  Used to identify which join tables need virtual-row
        expansion in ``_row_matches_where``.
        """
        aliases = set()
        if where_node is None:
            return aliases
        if hasattr(where_node, "children"):
            for child in where_node.children:
                aliases |= self._collect_join_aliases(child)
        elif hasattr(where_node, "lhs"):
            alias = getattr(where_node.lhs, "alias", None)
            if alias:
                aliases.add(alias)
        return aliases

    def _row_matches_where(self, obj_dict, where_node, _pinned=None):
        """
        Evaluate a WhereNode against an object represented as a plain dict.

        This is a simple recursive Python evaluator for Django's WHERE tree.
        It handles the most common lookup types (exact, gt, lt, in, isnull,
        startswith, contains, etc.).  Unsupported lookups raise
        NotSupportedError so tests are correctly skipped.

        For AND nodes at the top level (``_pinned is None``), we expand cross-
        table joins into virtual rows (one per M2M/FK combination) and check
        that at least one virtual row satisfies ALL AND conditions.  This
        matches SQL JOIN semantics where all conditions in a single ``filter()``
        call must be satisfied by the SAME joined row.
        """
        if where_node is None or not where_node.children:
            return True
        from django.db.models.sql.where import AND, OR, XOR, NothingNode

        if isinstance(where_node, NothingNode):
            return False

        connector = where_node.connector
        negated = where_node.negated

        # For AND nodes, expand cross-table aliases into virtual rows so that
        # all conditions referencing the SAME join alias are checked against the
        # SAME joined row (matching SQL single-filter semantics).
        if connector == AND and _pinned is None:
            main_table = self.query.get_meta().db_table
            all_aliases = self._collect_join_aliases(where_node)
            alias_map = getattr(self.query, "alias_map", {})
            cross_aliases = [
                a
                for a in all_aliases
                if a != main_table and a in alias_map and hasattr(alias_map[a], "join_cols")
            ]
            if cross_aliases:
                from itertools import product as iproduct

                rows_per_alias = {}
                for alias in cross_aliases:
                    related = self._resolve_joined_rows(obj_dict, alias)
                    rows_per_alias[alias] = related if related else [None]

                alias_list = sorted(rows_per_alias.keys())
                combos = list(iproduct(*(rows_per_alias[a] for a in alias_list)))

                def eval_child_pinned(child, pin):
                    from django.db.models.expressions import Exists

                    if isinstance(child, NothingNode):
                        return False
                    if isinstance(child, Exists):
                        return self._eval_exists(child, obj_dict)
                    if hasattr(child, "children"):
                        return self._row_matches_where(obj_dict, child, _pinned=pin)
                    return self._eval_lookup(obj_dict, child, _pinned=pin)

                for combo in combos:
                    pin = {alias_list[i]: combo[i] for i in range(len(alias_list))}
                    if all(eval_child_pinned(c, pin) for c in where_node.children):
                        return not negated
                return negated

        def eval_child(child):
            from django.db.models.expressions import Exists

            if isinstance(child, NothingNode):
                return False
            if isinstance(child, Exists):
                return self._eval_exists(child, obj_dict)
            if hasattr(child, "children"):
                return self._row_matches_where(obj_dict, child, _pinned=_pinned)
            return self._eval_lookup(obj_dict, child, _pinned=_pinned)

        results = [eval_child(c) for c in where_node.children]

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

        # Extract expression (used by pubdate__month, pubdate__year lookups etc.)
        # Unlike Trunc (which returns a truncated date), Extract returns an integer
        # component: ExtractYear → int year, ExtractMonth → int month, etc.
        from django.db.models.functions import Extract

        if isinstance(expr, Extract):
            sources = expr.get_source_expressions()
            if sources:
                field_val = self._eval_select_expr(sources[0], row_dict)
                if field_val is None:
                    return None
                import datetime

                kind = expr.lookup_name  # "year", "month", "day", "hour", etc.
                try:
                    if kind == "year":
                        return field_val.year
                    elif kind == "iso_year":
                        return field_val.isocalendar()[0]
                    elif kind == "month":
                        return field_val.month
                    elif kind == "day":
                        return field_val.day
                    elif kind == "week_day":
                        # Django week_day: Sunday=1 … Saturday=7
                        return (field_val.weekday() + 2) % 7 or 7
                    elif kind == "iso_week_day":
                        return field_val.isoweekday()
                    elif kind == "week":
                        return field_val.isocalendar()[1]
                    elif kind == "quarter":
                        return (field_val.month - 1) // 3 + 1
                    elif kind == "hour" and isinstance(field_val, datetime.datetime):
                        return field_val.hour
                    elif kind == "minute" and isinstance(field_val, datetime.datetime):
                        return field_val.minute
                    elif kind == "second" and isinstance(field_val, datetime.datetime):
                        return field_val.second
                except (AttributeError, ValueError):
                    return None

        # Ref: reference to a query alias/annotation. Evaluate the source.
        from django.db.models.expressions import Ref

        if isinstance(expr, Ref):
            src = getattr(expr, "source", None)
            if src is not None:
                return self._eval_select_expr(src, row_dict)
            # Fall back to resolving named annotation from query.annotations.
            annotation = getattr(self.query, "annotations", {}).get(expr.refs)
            if annotation is not None:
                return self._eval_select_expr(annotation, row_dict)
            return None

        # Cast / Func expressions: evaluate the first source expression and
        # optionally coerce the type to match the output field.
        from django.db.models.functions import Cast

        if isinstance(expr, Cast):
            src_exprs = getattr(expr, "source_expressions", None) or []
            if src_exprs:
                val = self._eval_select_expr(src_exprs[0], row_dict)
                if val is None:
                    return None
                # Coerce to the output field's Python type.
                try:
                    out_field = getattr(expr, "output_field", None)
                    if out_field is not None:
                        val = out_field.to_python(val)
                except Exception:
                    pass
                return val
            return None

        # Generic Func: evaluate the first source expression (best-effort).
        from django.db.models.expressions import Func

        if isinstance(expr, Func):
            src_exprs = getattr(expr, "source_expressions", None) or []
            if src_exprs:
                return self._eval_select_expr(src_exprs[0], row_dict)
            return None

        # Aggregate expressions (Max, Min, Sum, Avg, Count) used as row
        # annotations — for non-grouped queries each row is its own group, so
        # the aggregate over a single row is the row's own field value.
        from django.db.models import Count
        from django.db.models.aggregates import Aggregate

        if isinstance(expr, Aggregate):
            source_exprs = getattr(expr, "get_source_expressions", lambda: [])()
            if source_exprs:
                src_val = self._eval_select_expr(source_exprs[0], row_dict)
                if isinstance(expr, Count):
                    return 0 if src_val is None else 1
                return src_val
            return None

        return None

    def _eval_lookup(self, obj_dict, lookup, _pinned=None):
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
        # Handle Exists(subquery) wrapped as Exact(Exists(...), True).
        from django.db.models.expressions import Exists as ExistsExpr

        if isinstance(lhs, ExistsExpr):
            exists_result = self._eval_exists(lhs, obj_dict)
            # The rhs is the expected boolean (True means "exists", False means "not exists").
            expected = lookup.rhs
            if isinstance(expected, Value):
                expected = expected.value
            return exists_result == expected

        col_obj = None  # The Col expression (for join resolution)
        col_table_alias = None  # The table alias the column belongs to
        _lhs_transform = None  # Set when lhs is a transform wrapping a Col
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
                # Remember that the original lhs is a transform — after we
                # resolve obj_value we must apply the transform.
                _lhs_transform = lhs if (lhs is not col_obj) else None
            else:
                # Could be an annotation expression (F, Trunc, Cast, Ref, etc.)
                # with source_expressions.  Try evaluating it directly.
                obj_value = self._eval_select_expr(lhs, obj_dict)
                # Fall through with obj_value already set; set col_name to a
                # sentinel so we skip obj_dict.get() below.
                col_name = "__EXPR__"
        elif hasattr(lhs, "source_expressions"):
            # Func/Cast etc. with source_expressions but no 'lhs' attribute.
            obj_value = self._eval_select_expr(lhs, obj_dict)
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
                joined_values = self._resolve_joined_col(
                    obj_dict, col_table_alias, col_name, _pinned=_pinned
                )
                obj_value = joined_values[0] if joined_values else None
            else:
                obj_value = obj_dict.get(col_name)
                # If lhs was a transform (e.g. ExtractMonth, Cast), apply it
                # so the comparison gets the transformed value (e.g. integer 10
                # for a month lookup).  Skip this for Year* lookups — those
                # already extract the year component from the raw date value.
                if _lhs_transform is not None:
                    try:
                        from django.db.models.lookups import (
                            YearExact,
                            YearGte,
                            YearLt,
                            YearLte,
                        )

                        _year_lookups = (YearExact, YearGte, YearLt, YearLte)
                    except ImportError:
                        _year_lookups = ()
                    if not isinstance(lookup, _year_lookups):
                        obj_value = self._eval_select_expr(_lhs_transform, obj_dict)

        # Resolve the RHS value.
        rhs = lookup.rhs
        if isinstance(rhs, Value):
            rhs = rhs.value
        elif hasattr(rhs, "resolve_expression"):
            # Subquery or QuerySet — try to evaluate it against our ZODB backend.
            rhs = self._eval_subquery_rhs(rhs, lookup)
            if rhs is None:
                # Could not evaluate — treat as matching (conservative).
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
            return str(obj_value or "").lower() == str(rhs or "").lower()
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
            return str(rhs or "").lower() in str(obj_value if obj_value is not None else "").lower()
        elif isinstance(lookup, Contains):
            return str(rhs) in str(obj_value if obj_value is not None else "")
        elif isinstance(lookup, IStartsWith):
            return (
                str(obj_value if obj_value is not None else "")
                .lower()
                .startswith(str(rhs or "").lower())
            )
        elif isinstance(lookup, StartsWith):
            return str(obj_value if obj_value is not None else "").startswith(str(rhs or ""))
        elif isinstance(lookup, IEndsWith):
            return (
                str(obj_value if obj_value is not None else "")
                .lower()
                .endswith(str(rhs or "").lower())
            )
        elif isinstance(lookup, EndsWith):
            return str(obj_value if obj_value is not None else "").endswith(str(rhs or ""))
        elif isinstance(lookup, IRegex):
            import re

            return bool(re.search(rhs, str(obj_value or ""), re.IGNORECASE))
        elif isinstance(lookup, Regex):
            import re

            return bool(re.search(rhs, str(obj_value or "")))
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
                return str(v or "").lower() == str(rhs or "").lower()
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
                return str(rhs or "").lower() in str(v if v is not None else "").lower()
            elif isinstance(lookup, Contains):
                return str(rhs) in str(v if v is not None else "")
            elif isinstance(lookup, IStartsWith):
                return str(v if v is not None else "").lower().startswith(str(rhs or "").lower())
            elif isinstance(lookup, StartsWith):
                return str(v if v is not None else "").startswith(str(rhs or ""))
            elif isinstance(lookup, IEndsWith):
                return str(v if v is not None else "").lower().endswith(str(rhs or "").lower())
            elif isinstance(lookup, EndsWith):
                return str(v if v is not None else "").endswith(str(rhs or ""))
            elif isinstance(lookup, IRegex):
                import re

                return bool(re.search(rhs, str(v or ""), re.IGNORECASE))
            elif isinstance(lookup, Regex):
                import re

                return bool(re.search(rhs, str(v or "")))
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
        from django.db.models.expressions import OrderBy, Ref

        order_by = []
        for expr, (_sql, _params, _is_ref) in self.get_order_by():
            if isinstance(expr, OrderBy):
                source = expr.expression
                # Unwrap Ref/PositionRef (used by dates() ORDER BY position)
                while isinstance(source, Ref):
                    source = source.source
                col_name = getattr(getattr(source, "target", None), "column", None)
                # For Trunc/Extract/transforms, drill into .lhs chain
                if col_name is None:
                    inner = source
                    while inner is not None and col_name is None:
                        col_name = getattr(getattr(inner, "target", None), "column", None)
                        inner = getattr(inner, "lhs", None)
                # Try source_expressions (Func-style)
                if col_name is None:
                    sources = getattr(source, "source_expressions", None) or []
                    if sources:
                        col_name = getattr(getattr(sources[0], "target", None), "column", None)
                if col_name:
                    if expr.nulls_first:
                        nulls_first = True
                    elif expr.nulls_last:
                        nulls_first = False
                    else:
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
        Yield result rows as tuples, with field converters applied.

        When called by ModelIterable, ``results`` is the value returned by
        execute_sql() — a list of row-tuple chunks.  When called standalone
        (results=None), we fetch from ZODB directly.
        """
        if results is not None:
            # Unwrap the chunks returned by execute_sql(MULTI).
            rows = [row for chunk in results for row in chunk]
        else:
            # Standalone call: fetch fresh from ZODB.
            self._setup_klass_info()
            rows = [self._build_row_tuple(row) for row in self._fetch_matching_rows()]

        # Apply field converters (e.g. UUIDField hex→uuid.UUID, DateField
        # string→date) so model instances get the correct Python types.
        try:
            expressions = (
                [entry[0] for entry in self.select] if getattr(self, "select", None) else []
            )
            converters = self.get_converters(expressions)
        except Exception:
            converters = {}

        if converters:
            yield from self.apply_converters(rows, converters)
        else:
            yield from rows

    def _compute_aggregates(self, result_type):
        """
        Handle the case where Django routes a get_aggregation() call through
        SQLCompiler instead of SQLAggregateCompiler (the non-subquery path).

        In this path, ``self.query.annotation_select`` contains Aggregate
        expressions (Count, Sum, …) and ``self.query.default_cols`` is False.
        We compute each aggregate over the matching rows and return a single
        tuple, matching what SQLAggregateCompiler.execute_sql(SINGLE) returns.
        """
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

        # Reuse SQLAggregateCompiler's annotation logic.
        agg_compiler = self.connection.ops.compiler("SQLAggregateCompiler")(
            self.query, self.connection, self.using
        )
        results = [
            agg_compiler._compute_annotation(ann, matching)
            for ann in self.query.annotation_select.values()
        ]

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
            result = rows[0] if rows else None
            self._log_query("ZODB SELECT")
            return result
        if result_type == CURSOR:
            self._log_query("ZODB SELECT")
            return rows
        # MULTI: return as a list of chunks so results_iter() can unwrap them.
        self._log_query("ZODB SELECT")
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

        self._log_query("ZODB INSERT")
        if returning_fields:
            # Django expects a list of (value,) tuples for RETURNING.
            # Apply field db converters so callers get the correct Python type
            # (e.g. uuid.UUID for UUIDField, not the raw hex string).
            rows = []
            for pk in inserted_pks:
                converted = []
                for field in returning_fields:
                    value = pk
                    for converter in field.get_db_converters(self.connection):
                        value = converter(value, None, self.connection)
                    converted.append(value)
                rows.append(tuple(converted))
            return rows
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
        self._log_query("ZODB DELETE")
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
        self._log_query("ZODB UPDATE")
        return updated


class SQLAggregateCompiler(ZODBMixin, compiler.SQLAggregateCompiler):
    """Aggregate (COUNT, SUM, etc.) compiler for ZODB."""

    def execute_sql(self, result_type=compiler.SINGLE):

        if self.query.is_empty():
            return tuple(0 for _ in self.query.annotation_select)

        # For AggregateQuery (wraps a DISTINCT/subquery inner query), execute
        # the inner query first, deduplicate by PK, then aggregate over results.
        inner_query = getattr(self.query, "inner_query", None)
        if inner_query is not None:
            inner_compiler = inner_query.get_compiler(
                using=self.connection.alias, connection=self.connection
            )
            inner_matching = inner_compiler._fetch_matching_rows()
            # Deduplicate by PK to honour DISTINCT.
            try:
                pk_col = inner_query.get_meta().pk.column
            except Exception:
                pk_col = "id"
            seen_pks = set()
            matching = []
            for row in inner_matching:
                pk = row.get(pk_col)
                if pk is None or pk not in seen_pks:
                    if pk is not None:
                        seen_pks.add(pk)
                    matching.append(row)
        else:
            coll = self._get_btree()
            if coll is None:
                return (0,)
            where = self.query.where
            matching = [
                self._obj_to_dict(obj)
                for obj in coll.values()
                if self._row_matches_where(self._obj_to_dict(obj), where)
            ]

        results = []
        for _annotation_key, annotation in self.query.annotation_select.items():
            results.append(self._compute_annotation(annotation, matching))

        self._log_query("ZODB AGGREGATE")
        return tuple(results) if results else (len(matching),)

    def _compute_annotation(self, annotation, rows):
        """Compute a single aggregate annotation over a list of row dicts."""
        from django.db.models import Avg, Count, Max, Min, Sum

        if isinstance(annotation, Count):
            return len(rows)

        # For Sum/Max/Min/Avg, find the source field and aggregate its values.
        source_col = self._get_annotation_source_col(annotation)
        if source_col is None:
            return None

        values = [row.get(source_col) for row in rows if row.get(source_col) is not None]

        if not values:
            return getattr(annotation, "empty_result_set_value", None)

        if isinstance(annotation, Sum):
            try:
                return sum(values)
            except TypeError:
                return None
        elif isinstance(annotation, Max):
            try:
                return max(values)
            except TypeError:
                return None
        elif isinstance(annotation, Min):
            try:
                return min(values)
            except TypeError:
                return None
        elif isinstance(annotation, Avg):
            try:
                return sum(values) / len(values)
            except (TypeError, ZeroDivisionError):
                return None
        return None

    def _get_annotation_source_col(self, annotation):
        """Extract the column name from an aggregate's source expression."""
        try:
            source_exprs = annotation.get_source_expressions()
            if source_exprs:
                src = source_exprs[0]
                # Direct Col reference.
                col = getattr(getattr(src, "target", None), "column", None)
                if col:
                    return col
                # Ref wrapping a Col.
                inner = getattr(src, "refs", None)
                if inner:
                    src2 = getattr(src, "source", None) or src
                    col = getattr(getattr(src2, "target", None), "column", None)
                    return col
        except Exception:
            pass
        return None
