from django.db.backends.base.features import BaseDatabaseFeatures
from django.utils.functional import cached_property


class DatabaseFeatures(BaseDatabaseFeatures):
    """
    Declare which Django ORM features ZODB supports (or does not).

    The strategy mirrors django-mongodb-backend: use feature flags to drive
    Django's built-in skip decorators, and supplement with explicit
    ``django_test_skips`` / ``django_test_expected_failures`` dicts for
    tests that require reasons beyond a boolean flag.

    Tests in ``django_test_skips`` are skipped because the feature is
    fundamentally unsupported or not yet implemented in this backend.
    Tests in ``django_test_expected_failures`` are known to produce wrong
    results at POC stage but should eventually pass.

    Remove entries from these dicts as the backend matures.
    """

    # ── Core identity ─────────────────────────────────────────────────────────
    # ZODB uses Python's native integer PKs via OOBTree (supports int and string PKs).
    # Unlike MongoDB's ObjectId, we keep Django's standard BigAutoField.
    interprets_empty_strings_as_nulls = False
    is_sql_auto_id = False

    # ── Supported features ────────────────────────────────────────────────────
    can_return_rows_from_bulk_insert = True
    has_bulk_insert = True
    supports_unspecified_pk = True

    # ── Unsupported SQL features ──────────────────────────────────────────────
    allow_sliced_subqueries_with_in = False
    allows_multiple_constraints_on_same_fields = False
    can_create_inline_fk = False
    can_introspect_check_constraints = False
    can_introspect_foreign_keys = False
    has_native_duration_field = False
    has_native_json_field = False  # stored as plain Python dicts
    has_native_uuid_field = False
    has_select_for_update = False
    has_select_for_update_nowait = False
    has_select_for_update_of = False
    has_select_for_update_skip_locked = False
    supports_aggregate_filter_clause = False
    supports_collation_on_charfield = False
    supports_column_check_constraints = False
    supports_covering_indexes = False
    supports_date_lookup_using_string = False
    supports_deferrable_unique_constraints = False
    supports_explaining_query_execution = False
    supports_expression_defaults = False
    supports_expression_indexes = False
    supports_foreign_keys = False
    supports_frame_range_fixed_distance = False
    supports_ignore_conflicts = False
    supports_index_on_text_field = False
    supports_nulls_distinct_unique_constraints = False
    supports_over_clause = False  # no window functions
    supports_paramstyle_pyformat = False
    supports_partial_indexes_together = False
    supports_select_for_update_with_of = False
    supports_select_related = False  # JOINs not yet implemented
    supports_sequence_reset = False
    supports_subqueries_in_group_by = False
    supports_table_check_constraints = False
    supports_temporal_subtraction = False
    supports_timezones = False
    supports_transactions = True  # ZODB has proper transaction + savepoint support
    supports_update_conflicts = False
    uses_savepoints = True  # transaction.savepoint() / sp.rollback() works in ZODB
    uses_sequences = False

    # ── Test-database configuration ───────────────────────────────────────────
    can_clone_databases = False
    test_db_allows_multiple_connections = False

    # ── Explicit skip / xfail declarations ───────────────────────────────────
    # Pattern from django-mongodb-backend: reason → set of dotted test labels.
    # Entries here cause the test runner to skip those tests with the given
    # reason message.  Prefer whole test-class labels where possible.

    _django_test_skips = {
        # ── SQL raw queries ───────────────────────────────────────────────────
        "ZODB does not support raw SQL queries.": {
            "raw_query.tests.RawQueryTests",
        },
        # ── SQL inspection / EXPLAIN ──────────────────────────────────────────
        "ZODB does not support SQL EXPLAIN.": {
            "backends.tests.BackendTestCase.test_queries_logger",
        },
        "Test inspects SQL query string.": {
            "admin_changelist.tests.ChangeListTests.test_changelist_view_list_editable_changed_objects_uses_filter",
            "admin_changelist.tests.ChangeListTests.test_many_search_terms",
            "admin_changelist.tests.ChangeListTests.test_search_with_exact_lookup_for_non_string_field",
            "aggregation.tests.AggregateAnnotationPruningTests",
            "aggregation.tests.AggregateTestCase.test_count_star",
            "delete.tests.DeletionTests.test_only_referenced_fields_selected",
            "expressions.tests.ExistsTests.test_optimizations",
            "lookup.tests.LookupTests.test_in_ignore_none",
            "lookup.tests.LookupTests.test_lookup_direct_value_rhs_unwrapped",
            "lookup.tests.LookupTests.test_textfield_exact_null",
            "many_to_many.tests.ManyToManyQueryTests.test_count_join_optimization_disabled",
            "many_to_many.tests.ManyToManyQueryTests.test_exists_join_optimization_disabled",
            "many_to_many.tests.ManyToManyTests.test_custom_default_manager_exists_count",
            "many_to_one.tests.ManyToOneTests.test_selects",
            "migrations.test_commands.MigrateTests.test_migrate_syncdb_deferred_sql_executed_with_schemaeditor",
            "ordering.tests.OrderingTests.test_order_by_f_expression_duplicates",
            "queries.tests.ExistsSql.test_exists",
            "queries.tests.Queries6Tests.test_col_alias_quoted",
            "schema.tests.SchemaTests.test_rename_column_renames_deferred_sql_references",
            "schema.tests.SchemaTests.test_rename_table_renames_deferred_sql_references",
        },
        "Test checks str(queryset.query) for SQL.": {
            "aggregation_regress.tests.AggregationTests.test_more_more",
            "aggregation_regress.tests.AggregationTests.test_reverse_join_trimming",
            "aggregation_regress.tests.JoinPromotionTests",
            "custom_lookups.tests.YearLteTests",
            "expressions.tests.BasicExpressionsTests.test_subquery_sql",
            "expressions.tests.BasicExpressionsTests.test_ticket_18375_chained_filters",
            "expressions.tests.BasicExpressionsTests.test_ticket_18375_join_reuse",
            "expressions.tests.BasicExpressionsTests.test_ticket_18375_kwarg_ordering",
            "expressions.tests.BasicExpressionsTests.test_ticket_18375_kwarg_ordering_2",
            "expressions_case.tests.CaseExpressionTests.test_m2m_reuse",
            "filtered_relation.tests.FilteredRelationTests.test_internal_queryset_alias_mapping",
            "generic_relations_regress.tests.GenericRelationTests.test_join_reuse",
            "lookup.tests.LookupTests.test_in_keeps_value_ordering",
            "model_inheritance.tests.ModelInheritanceTests.test_inherited_ordering_pk_desc",
            "queries.tests.DisjunctionPromotionTests",
            "queries.tests.JoinReuseTest",
            "queries.tests.NullJoinPromotionOrTest.test_null_join_demotion",
            "queries.tests.NullableRelOrderingTests.test_join_already_in_query",
            "queries.tests.Queries1Tests.test_order_by_join_unref",
            "queries.tests.Queries1Tests.test_subquery_condition",
            "queries.tests.Queries4Tests.test_order_by_resetting",
            "queries.tests.Queries6Tests.test_nested_queries_sql",
            "queries.tests.Queries6Tests.test_ticket_11320",
            "queries.tests.ReverseJoinTrimmingTest.test_reverse_trimming",
            "queries.tests.ValuesJoinPromotionTests",
            "queries.tests.Ticket18785Tests.test_ticket_18785",
            "select_related_regress.tests.SelectRelatedRegressTests.test_null_join_promotion",
            "select_related_regress.tests.SelectRelatedRegressTests.test_regression_7110",
        },
        # ── cursor.execute() ──────────────────────────────────────────────────
        "ZODB does not support cursor.execute() for SQL DML.": {
            "backends.tests.BackendTestCase.test_duplicate_table_error",
            "backends.tests.BackendTestCase.test_cursor_contextmanager",
            "backends.base.test_base.ExecuteWrapperTests",
            "migrations.test_commands.MigrateTests.test_migrate_plan",
        },
        "ZODB does not support cursor.callproc().": {
            "backends.test_utils.CursorWrapperTests.test_unsupported_callproc_kparams_raises_error",
        },
        # ── SELECT FOR UPDATE ─────────────────────────────────────────────────
        "ZODB does not support SELECT FOR UPDATE.": {
            "select_for_update.tests.SelectForUpdateTests",
        },
        # ── Transactions (SQL-specific behaviour) ─────────────────────────────
        # ZODB supports transactions and savepoints, but these tests check
        # SQL-specific semantics (broken-transaction state, autocommit guards,
        # etc.) that don't map to ZODB's transaction model.
        "SQL transaction semantics tests not applicable to ZODB.": {
            "transactions.tests.AtomicErrorsTests",
            "transactions.tests.AtomicMiscTests",
            "transactions.tests.AtomicMergeTests",
            "transactions.tests.AtomicWithoutAutocommitTests",
            "transactions.tests.AtomicInsideTransactionTests",
            "transactions.tests.NonAutocommitTests",
        },
        # ── assertNumQueries (SQL query counter) ──────────────────────────────
        # assertNumQueries() counts cursor.execute() calls, which is always 0
        # for ZODB since no SQL is emitted.
        "assertNumQueries counts SQL cursor calls; ZODB emits none.": {
            "admin_utils.test_logentry.LogEntryTests.test_log_actions",
            "admin_utils.tests.NestedObjectsTests.test_on_delete_do_nothing",
            "admin_utils.tests.NestedObjectsTests.test_queries",
            "auth_tests.test_management.CreatePermissionsMultipleDatabasesTests.test_set_permissions_fk_to_using_parameter",
            "auth_tests.test_middleware.TestLoginRequiredMiddleware.test_protected_view_logged_in_performance",
            "basic.tests.ModelInstanceCreationTests.test_save_parent_primary_with_default",
            "basic.tests.ModelInstanceCreationTests.test_save_primary_with_default",
            "basic.tests.ModelInstanceCreationTests.test_save_primary_with_default_force_update",
            "basic.tests.SelectOnSaveTests.test_select_on_save",
            "basic.tests.SelectOnSaveTests.test_select_on_save_lying_update",
            "basic.tests.ModelTest.test_save_expressions",
            "defer_regress.tests.DeferRegressionTest.test_basic",
            "defer_regress.tests.DeferRegressionTest.test_resolve_columns",
            "generic_views.test_dates.ArchiveIndexViewTests.test_no_duplicate_query",
            "generic_views.test_dates.ArchiveIndexViewTests.test_paginated_archive_view_does_not_load_entire_table",
            "generic_views.test_dates.DateDetailViewTests.test_get_object_custom_queryset_numqueries",
            "generic_views.test_dates.YearArchiveViewTests.test_no_duplicate_query",
            "generic_views.test_list.ListViewTests.test_paginated_list_view_does_not_load_entire_table",
            "model_formsets_regress.tests.FormsetTests.test_extraneous_query_is_not_run",
            "modeladmin.tests.ModelAdminTests.test_log_deletions",
            "queryset_pickle.tests.PickleabilityTestCase.test_pickle_prefetch_queryset_usable_outside_of_prefetch",
            "select_related_onetoone.tests.ReverseSelectRelatedTestCase.test_nullable_relation",
            "select_related_onetoone.tests.ReverseSelectRelatedTestCase.test_parent_only",
            "test_utils.tests.AssertNumQueriesContextManagerTests",
            "test_utils.tests.AssertNumQueriesTests.test_assert_num_queries_with_client",
            "test_utils.tests.AssertNumQueriesUponConnectionTests.test_ignores_connection_configuration_queries",
        },
        # ── cursor.fetchone() used directly ───────────────────────────────────
        "Test calls cursor.fetchone() expecting a SQL result row.": {
            "test_utils.tests.AllowedDatabaseQueriesTests.test_allowed_database_copy_queries",
        },
        # ── REMOTE_USER authentication ────────────────────────────────────────
        "RemoteUserBackend tests rely on HTTP_REMOTE_USER header session state.": {
            "auth_tests.test_remote_user.AllowAllUsersRemoteUserBackendTest.test_header_disappears",
            "auth_tests.test_remote_user.AllowAllUsersRemoteUserBackendTest.test_header_disappears_async",
            "auth_tests.test_remote_user.CustomHeaderRemoteUserTest.test_header_disappears",
            "auth_tests.test_remote_user.CustomHeaderRemoteUserTest.test_header_disappears_async",
            "auth_tests.test_remote_user.RemoteUserCustomTest.test_header_disappears",
            "auth_tests.test_remote_user.RemoteUserCustomTest.test_header_disappears_async",
            "auth_tests.test_remote_user.RemoteUserNoCreateTest.test_header_disappears",
            "auth_tests.test_remote_user.RemoteUserNoCreateTest.test_header_disappears_async",
            "auth_tests.test_remote_user.RemoteUserTest.test_header_disappears",
            "auth_tests.test_remote_user.RemoteUserTest.test_header_disappears_async",
        },
        # ── Date ordering returns None (date fields stored as Python objects) ─
        "Date ordering with None values not yet supported.": {
            "generic_views.test_dates.ArchiveIndexViewTests.test_date_list_order",
            "generic_views.test_dates.MonthArchiveViewTests.test_date_list_order",
            "generic_views.test_dates.YearArchiveViewTests.test_date_list_order",
        },
        # ── select_related validation (BigAutoField.col) ──────────────────────
        "select_related field validation inspects Col internals not yet supported.": {
            "select_related_onetoone.tests.ReverseSelectRelatedValidationTests.test_reverse_related_validation",
            "select_related_onetoone.tests.ReverseSelectRelatedValidationTests.test_reverse_related_validation_with_filtered_relation",
        },
        # ── Window functions ──────────────────────────────────────────────────
        "ZODB does not support window functions.": {
            "expressions_window.tests.WindowFunctionTests",
        },
        # ── DISTINCT ON ───────────────────────────────────────────────────────
        "ZODB does not support SQL DISTINCT ON.": {
            "distinct_on_fields.tests.DistinctOnTests",
        },
        # ── QuerySet.extra() ──────────────────────────────────────────────────
        "ZODB does not support QuerySet.extra().": {
            "extra_regress.tests.ExtraRegressTests",
        },
        # ── GIS ───────────────────────────────────────────────────────────────
        "ZODB does not support GIS.": {
            "gis_tests",
        },
        # ── inspectdb ────────────────────────────────────────────────────────
        "inspectdb is not supported.": {
            "inspectdb.tests.InspectDBTestCase",
            "inspectdb.tests.InspectDBTransactionalTests",
        },
        # ── Introspection ─────────────────────────────────────────────────────
        "DatabaseIntrospection.get_table_description() not implemented.": {
            "introspection.tests.IntrospectionTests.test_bigautofield",
            "introspection.tests.IntrospectionTests.test_get_table_description_col_lengths",
            "introspection.tests.IntrospectionTests.test_get_table_description_names",
            "introspection.tests.IntrospectionTests.test_get_table_description_nullable",
            "introspection.tests.IntrospectionTests.test_get_table_description_types",
            "introspection.tests.IntrospectionTests.test_smallautofield",
            "introspection.tests.IntrospectionTests.test_sequence_list",
            "introspection.tests.IntrospectionTests.test_get_primary_key_column",
            "introspection.tests.IntrospectionTests.test_table_names_with_views",
        },
        # ── Schema / DDL (not yet implemented) ────────────────────────────────
        "Schema DDL tests not yet implemented for ZODB.": {
            "schema.tests.SchemaTests",
        },
        # ── Migrations (SQL-based) ────────────────────────────────────────────
        "SQL-based migrations are not supported.": {
            "migrations.test_commands.MigrateTests",
            "migrations.test_executor.ExecutorTests",
            "migrations.test_operations.OperationTests",
            "migrate_signals.tests.MigrateSignalTests",
            "migration_test_data_persistence.tests.MigrationDataPersistenceTestCase",
            "migration_test_data_persistence.tests.MigrationDataPersistenceClassSetup",
            "migration_test_data_persistence.tests.MigrationDataNormalPersistenceTestCase",
        },
        # ── Constraints (DB-level enforcement) ───────────────────────────────
        "Database-level constraint enforcement is not supported in ZODB.": {
            "constraints.tests.CheckConstraintTests",
            "constraints.tests.UniqueConstraintTests",
        },
        # ── Database defaults ─────────────────────────────────────────────────
        "Database-level defaults are not supported.": {
            "basic.tests.ModelInstanceCreationTests.test_save_primary_with_db_default",
            "basic.tests.ModelInstanceCreationTests.test_save_primary_with_falsey_db_default",
            "constraints.tests.UniqueConstraintTests.test_database_default",
            "field_defaults.tests.DefaultTests",
            "validation.test_unique.PerformUniqueChecksTest.test_unique_db_default",
        },
        # ── Foreign objects / tuple lookups ──────────────────────────────────
        "ForeignObject is not supported.": {
            "foreign_object.test_agnostic_order_trimjoin.TestLookupQuery",
            "foreign_object.test_empty_join.RestrictedConditionsTests",
            "foreign_object.tests.MultiColumnFKTests",
            "foreign_object.tests.TestExtraJoinFilterQ",
            "foreign_object.test_forms.FormsTests",
        },
        "Tuple lookups are not supported.": {
            "foreign_object.test_tuple_lookups.TupleLookupsTests",
        },
        # ── Composite PKs ─────────────────────────────────────────────────────
        "Composite primary keys are not supported.": {
            "composite_pk.test_aggregate.CompositePKAggregateTests",
            "composite_pk.test_create.CompositePKCreateTests",
            "composite_pk.test_delete.CompositePKDeleteTests",
            "composite_pk.test_filter.CompositePKFilterTests",
            "composite_pk.test_get.CompositePKGetTests",
            "composite_pk.test_models.CompositePKModelsTests",
            "composite_pk.test_order_by.CompositePKOrderByTests",
            "composite_pk.test_update.CompositePKUpdateTests",
            "composite_pk.test_values.CompositePKValuesTests",
            "composite_pk.tests.CompositePKTests",
            "composite_pk.tests.CompositePKFixturesTests",
        },
        # ── Database caching ──────────────────────────────────────────────────
        "Database caching is not implemented.": {
            "cache.tests.CreateCacheTableForDBCacheTests",
            "cache.tests.DBCacheTests",
            "cache.tests.DBCacheWithTimeZoneTests",
        },
        # ── Backend-specific tests ────────────────────────────────────────────
        "Backend tests assume SQL connection/cursor API.": {
            "backends.tests.BackendTestCase.test_is_usable_after_database_disconnects",
            "backends.tests.BackendTestCase.test_cursor_contextmanager",
            "backends.tests.LastExecutedQueryTest",
            "backends.tests.ThreadTests.test_pass_connection_between_threads",
            "backends.tests.ThreadTests.test_default_connection_thread_local",
            "backends.base.test_base.ConnectionHealthChecksTests",
        },
        "Disallowed database query protection not applicable.": {
            # ZODB doesn't use cursor()/chunked_cursor() in the Django sense.
            "test_utils.test_testcase.TestTestCase.test_disallowed_database_queries",
            "test_utils.test_transactiontestcase.DisallowedDatabaseQueriesTests",
            "test_utils.tests.DisallowedDatabaseQueriesTests",
        },
        "connection.close() semantics differ in ZODB.": {
            "servers.test_liveserverthread.LiveServerThreadTest.test_closes_connections",
            "servers.tests.LiveServerTestCloseConnectionTest.test_closes_connections",
        },
        # ── Not yet implemented in POC ────────────────────────────────────────
        "Aggregation not yet implemented in ZODB POC.": {
            "aggregation.tests.AggregateTestCase",
            "aggregation_regress.tests.AggregationTests",
            "aggregation_regress.tests.JoinPromotionTests",
            "aggregation_regress.tests.SelfReferentialFKTests",
        },
        "Annotations not yet implemented in ZODB POC.": {
            "annotations.tests.NonAggregateAnnotationTestCase",
            "annotations.tests.AliasTests",
        },
        "Complex expressions not yet implemented in ZODB POC.": {
            "expressions.tests.BasicExpressionsTests",
            "expressions.tests.IterableLookupInnerExpressionsTests",
            "expressions.tests.FTests",
            "expressions.tests.ExpressionsTests",
            "expressions.tests.ExpressionsNumericTests",
            "expressions.tests.ExpressionOperatorTests",
            "expressions.tests.FTimeDeltaTests",
            "expressions.tests.ValueTests",
            "expressions.tests.ExistsTests",
            "expressions.tests.FieldTransformTests",
            "expressions.tests.ReprTests",
            "expressions.tests.OrderByTests",
            "expressions_case.tests.CaseExpressionTests",
            "expressions_case.tests.CaseDocumentationExamples",
            "expressions_case.tests.CaseWhenTests",
            "db_functions.tests.FunctionTests",
            "db_functions.comparison.test_cast.CastTests",
            "db_functions.comparison.test_coalesce.CoalesceTests",
            "db_functions.comparison.test_collate.CollateTests",
            "db_functions.comparison.test_greatest.GreatestTests",
            "db_functions.comparison.test_least.LeastTests",
            "db_functions.comparison.test_nullif.NullIfTests",
            "db_functions.datetime.test_extract_trunc.DateFunctionTests",
            "db_functions.datetime.test_extract_trunc.DateFunctionWithTimeZoneTests",
            "db_functions.datetime.test_now.NowTests",
            "db_functions.math.test_abs.AbsTests",
            "db_functions.math.test_acos.ACosTests",
            "db_functions.math.test_asin.ASinTests",
            "db_functions.math.test_atan.ATanTests",
            "db_functions.math.test_atan2.ATan2Tests",
            "db_functions.math.test_ceil.CeilTests",
            "db_functions.math.test_cos.CosTests",
            "db_functions.math.test_cot.CotTests",
            "db_functions.math.test_degrees.DegreesTests",
            "db_functions.math.test_exp.ExpTests",
            "db_functions.math.test_floor.FloorTests",
            "db_functions.math.test_ln.LnTests",
            "db_functions.math.test_log.LogTests",
            "db_functions.math.test_mod.ModTests",
            "db_functions.math.test_pi.PiTests",
            "db_functions.math.test_power.PowerTests",
            "db_functions.math.test_radians.RadiansTests",
            "db_functions.math.test_random.RandomTests",
            "db_functions.math.test_round.RoundTests",
            "db_functions.math.test_sign.SignTests",
            "db_functions.math.test_sin.SinTests",
            "db_functions.math.test_sqrt.SqrtTests",
            "db_functions.math.test_tan.TanTests",
            "db_functions.text.test_chr.ChrTests",
            "db_functions.text.test_concat.ConcatTests",
            "db_functions.text.test_left.LeftTests",
            "db_functions.text.test_length.LengthTests",
            "db_functions.text.test_lower.LowerTests",
            "db_functions.text.test_md5.MD5Tests",
            "db_functions.text.test_ord.OrdTests",
            "db_functions.text.test_pad.PadTests",
            "db_functions.text.test_repeat.RepeatTests",
            "db_functions.text.test_replace.ReplaceTests",
            "db_functions.text.test_reverse.ReverseTests",
            "db_functions.text.test_right.RightTests",
            "db_functions.text.test_sha1.SHA1Tests",
            "db_functions.text.test_sha224.SHA224Tests",
            "db_functions.text.test_sha256.SHA256Tests",
            "db_functions.text.test_sha384.SHA384Tests",
            "db_functions.text.test_sha512.SHA512Tests",
            "db_functions.text.test_strindex.StrIndexTests",
            "db_functions.text.test_substr.SubstrTests",
            "db_functions.text.test_trim.TrimTests",
            "db_functions.text.test_upper.UpperTests",
        },
        "Custom lookups not yet implemented in ZODB POC.": {
            "custom_lookups.tests.LookupTests",
            "custom_lookups.tests.BilateralTransformTests",
            "custom_lookups.tests.DateTimeLookupTests",
            "custom_lookups.tests.YearLteTests",
            "custom_lookups.tests.LookupTransformCallOrderTests",
            "custom_lookups.tests.CustomizedMethodsTests",
            "custom_lookups.tests.SubqueryTransformTests",
            "custom_lookups.tests.RegisterLookupTests",
        },
        # ── Ordering (unsupported features) ──────────────────────────────────
        "QuerySet.extra() is not supported in ZODB.": {
            "basic.tests.ModelTest.test_extra_method_select_argument_with_dashes",
            "basic.tests.ModelTest.test_extra_method_select_argument_with_dashes_and_values",
            "ordering.tests.OrderingTests.test_extra_ordering",
            "ordering.tests.OrderingTests.test_extra_ordering_quoting",
            "ordering.tests.OrderingTests.test_extra_ordering_with_table_name",
            "ordering.tests.OrderingTests.test_alias_with_period_shadows_table_name",
        },
        "Ordering by subquery is not supported in ZODB POC.": {
            "ordering.tests.OrderingTests.test_orders_nulls_first_on_filtered_subquery",
        },
        "Ordering by CASE/WHEN expression not yet implemented in ZODB POC.": {
            "ordering.tests.OrderingTests.test_order_by_case_when_constant_value",
        },
        # ── Subqueries not yet implemented ────────────────────────────────────
        "Subqueries not yet implemented in ZODB POC.": {
            "queries.test_q.QCheckTests",
            "queries.test_query.TestQueryNoModel",
        },
        # ── SQL query capture (connection.queries) ────────────────────────────
        # Django's CaptureQueriesContext/assertNumQueries counts cursor.execute()
        # calls; ZODB never calls cursor.execute(), so the count is always 0.
        "SQL query capture relies on cursor.execute() which ZODB never calls.": {
            "test_utils.tests.CaptureQueriesContextManagerTests",
            "context_processors.tests.DebugContextProcessorTests.test_sql_queries",
        },
        # ── JSON db functions not implemented ─────────────────────────────────
        "JSONArray/JSONObject db functions are not implemented in ZODB POC.": {
            "db_functions.json.test_json_array.JSONArrayTests",
            "db_functions.json.test_json_array.JSONArrayObjectTests",
            "db_functions.json.test_json_object.JSONObjectTests",
        },
        # ── select_related (JOIN-based preloading) ─────────────────────────────
        # ZODB uses lazy loading instead of SQL JOINs for related objects.
        # Tests expecting select_related() to prefetch all objects in a single
        # query will fail because we make one query per related access.
        "select_related() uses lazy loading in ZODB; JOIN preloading not supported.": {
            "select_related_onetoone.tests.ReverseSelectRelatedTestCase",
        },
        # ── UNIQUE constraint enforcement ─────────────────────────────────────
        "ZODB does not enforce DB-level UNIQUE constraints.": {
            "auth_tests.test_basic.BasicTestCase.test_unicode_username",
        },
        # ── Model validation: TextField max_length ────────────────────────────
        "TextField max_length SystemCheck warning test not applicable to ZODB.": {
            "invalid_models_tests.test_ordinary_fields.TextFieldTests.test_max_length_warning",
        },
    }

    @cached_property
    def django_test_skips(self):
        skips = super().django_test_skips
        skips.update(self._django_test_skips)
        return skips

    _django_test_expected_failures = set()

    @cached_property
    def django_test_expected_failures(self):
        expected_failures = super().django_test_expected_failures
        expected_failures.update(self._django_test_expected_failures)
        return expected_failures
