from django.db.backends.base.features import BaseDatabaseFeatures


class DatabaseFeatures(BaseDatabaseFeatures):
    """
    Declare which Django ORM features ZODB supports (or does not).

    The strategy mirrors django-mongodb-backend: use feature flags to skip or
    xfail tests in the Django test suite that rely on capabilities ZODB cannot
    provide (SQL, joins, window functions, etc.), and incrementally enable them
    as the backend matures.
    """

    # -------------------------------------------------------------------------
    # Core identity
    # -------------------------------------------------------------------------
    # ZODB uses Python's native integer PKs via BTrees.LOBTree (64-bit int).
    # Unlike MongoDB's ObjectId, we can keep Django's standard BigAutoField,
    # which means significantly fewer test-suite adaptations are needed.
    interprets_empty_strings_as_nulls = False
    is_sql_auto_id = False

    # -------------------------------------------------------------------------
    # What ZODB supports
    # -------------------------------------------------------------------------
    supports_transactions = True
    supports_select_related = False  # joins require SQL — not yet implemented
    supports_ignore_conflicts = False
    supports_update_conflicts = False
    supports_select_for_update_with_of = False
    has_select_for_update = False
    has_select_for_update_nowait = False
    has_select_for_update_skip_locked = False
    has_select_for_update_of = False

    # Sequences / autoincrement handled via BTrees.Length counter.
    uses_sequences = False

    # We use MappingStorage for tests (in-memory); no separate test DB needed.
    can_clone_databases = False
    test_db_allows_multiple_connections = False

    # -------------------------------------------------------------------------
    # SQL features ZODB does not have
    # -------------------------------------------------------------------------
    supports_subqueries_in_group_by = False
    supports_expression_indexes = False
    supports_partial_indexes_together = False
    supports_covering_indexes = False
    supports_index_on_text_field = False

    # No SQL DDL or raw SQL execution.
    can_create_inline_fk = False
    uses_savepoints = True  # ZODB supports savepoints natively

    # No SQL aggregate functions; aggregation must be done in Python.
    supports_aggregate_filter_clause = False
    supports_over_clause = False  # no window functions
    supports_frame_range_fixed_distance = False

    # No SQL EXPLAIN.
    supports_explaining_query_execution = False

    # ZODB stores Python objects — no SQL type casting.
    has_native_uuid_field = False
    has_native_duration_field = False
    has_native_json_field = False  # stored as plain Python dicts

    # Introspection.
    can_introspect_foreign_keys = False
    can_introspect_check_constraints = False

    # Bulk operations — implemented, but without ON CONFLICT support.
    has_bulk_insert = True

    # ZODB has no concept of NULL vs missing for integers/booleans.
    interprets_empty_strings_as_nulls = False

    # -------------------------------------------------------------------------
    # Test-suite skip / xfail declarations
    # -------------------------------------------------------------------------
    # Tests that the Django test runner will skip for this backend.
    # Modelled after django-mongodb-backend's django_test_skips pattern.
    @property
    def django_test_skips(self):
        skips = {
            "ZODB does not support SQL raw queries.": {
                "raw_query.tests.RawQueryTests",
            },
            "ZODB does not support select_for_update.": {
                "select_for_update.tests.SelectForUpdateTests",
            },
            "ZODB does not support window functions.": {
                "expressions_window.tests.WindowFunctionTests",
            },
            "ZODB does not support SQL DISTINCT ON.": {
                "distinct_on_fields.tests.DistinctOnTests",
            },
            "ZODB does not support QuerySet.extra().": {
                "extra_regress.tests.ExtraRegressTests",
            },
            "ZODB does not support GIS.": {
                "gis_tests",
            },
        }
        return skips

    @property
    def django_test_expected_failures(self):
        return {}
