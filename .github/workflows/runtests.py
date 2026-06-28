#!/usr/bin/env python
"""
Test runner for Django's test suite against the ZODB backend.

Sharding
--------
Running 146 test apps sequentially in separate subprocesses (the approach
used by django-mongodb-backend) takes 60–90 minutes. We instead:

1. Pass ALL apps in the shard to a single ``runtests.py`` invocation, which
   means only one Python/Django startup per shard.
2. Add ``--parallel`` so Django distributes test *cases* across CPU cores
   within each job.
3. Split the app list into N shards via a GitHub Actions matrix, giving N
   parallel jobs.

With 8 shards × 4 vCPUs each the full suite runs in ~8–12 minutes on CI.

Shard selection is controlled by two environment variables set by CI:
  DJANGO_TEST_SHARD        integer 0-based index of this shard  (default: 0)
  DJANGO_TEST_SHARDS       total number of shards               (default: 1)

To run locally without sharding:
  python runtests_.py

To simulate a specific shard locally:
  DJANGO_TEST_SHARD=2 DJANGO_TEST_SHARDS=8 python runtests_.py
"""

import os
import pathlib
import subprocess
import sys

# Full list of Django test apps — mirrors django-mongodb-backend's list.
# Apps that are structurally incompatible with ZODB (raw SQL, GIS, etc.)
# are handled via DatabaseFeatures.django_test_skips so they appear as
# skipped rather than absent from the report.
test_apps = [
    "admin_changelist",
    "admin_checks",
    "admin_custom_urls",
    "admin_docs",
    "admin_filters",
    "admin_inlines",
    "admin_ordering",
    "admin_scripts",
    "admin_utils",
    "admin_views",
    "admin_widgets",
    "aggregation",
    "aggregation_regress",
    "annotations",
    "apps",
    "async",
    "auth_tests",
    "backends",
    "basic",
    "bulk_create",
    "cache",
    "check_framework",
    "constraints",
    "contenttypes_tests",
    "context_processors",
    "custom_columns",
    "custom_lookups",
    "custom_managers",
    "custom_pk",
    "datatypes",
    "dates",
    "datetimes",
    "db_functions",
    "defer",
    "defer_regress",
    "delete",
    "delete_regress",
    "empty",
    "empty_models",
    "expressions",
    "expressions_case",
    "field_defaults",
    "file_storage",
    "file_uploads",
    "filtered_relation",
    "fixtures",
    "fixtures_model_package",
    "fixtures_regress",
    "flatpages_tests",
    "force_insert_update",
    "foreign_object",
    "forms_tests",
    "from_db_value",
    "generic_inline_admin",
    "generic_relations",
    "generic_relations_regress",
    "generic_views",
    "get_earliest_or_latest",
    "get_object_or_404",
    "get_or_create",
    "i18n",
    "indexes",
    "inline_formsets",
    "introspection",
    "invalid_models_tests",
    "known_related_objects",
    "lookup",
    "m2m_and_m2o",
    "m2m_intermediary",
    "m2m_multiple",
    "m2m_recursive",
    "m2m_regress",
    "m2m_signals",
    "m2m_through",
    "m2m_through_regress",
    "m2o_recursive",
    "managers_regress",
    "many_to_many",
    "many_to_one",
    "many_to_one_null",
    "max_lengths",
    "messages_tests",
    "migrate_signals",
    "migration_test_data_persistence",
    "migrations",
    "model_fields",
    "model_forms",
    "model_formsets",
    "model_formsets_regress",
    "model_indexes",
    "model_inheritance",
    "model_inheritance_regress",
    "model_options",
    "model_package",
    "model_regress",
    "model_utils",
    "modeladmin",
    "multiple_database",
    "mutually_referential",
    "nested_foreign_keys",
    "null_fk",
    "null_fk_ordering",
    "null_queries",
    "one_to_one",
    "or_lookups",
    "order_with_respect_to",
    "ordering",
    "pagination",
    "prefetch_related",
    "proxy_model_inheritance",
    "proxy_models",
    "queries",
    "queryset_pickle",
    "redirects_tests",
    "reserved_names",
    "reverse_lookup",
    "save_delete_hooks",
    "schema",
    "select_for_update",
    "select_related",
    "select_related_onetoone",
    "select_related_regress",
    "serializers",
    "servers",
    "sessions_tests",
    "shortcuts",
    "signals",
    "sitemaps_tests",
    "sites_framework",
    "sites_tests",
    "string_lookup",
    "swappable_models",
    "syndication_tests",
    "test_client",
    "test_client_regress",
    "test_runner",
    "test_utils",
    "timezones",
    "transactions",
    "unmanaged_models",
    "update",
    "update_only_fields",
    "user_commands",
    "validation",
    "view_tests",
    "xor_lookups",
    # Backend-specific tests (apps under django_zodb_backend/tests/).
    *(
        sorted(
            x.name
            for x in (pathlib.Path(__file__).parent.parent.parent.resolve() / "tests").iterdir()
            if x.is_dir() and not x.name.startswith("_")
        )
        if (pathlib.Path(__file__).parent.parent.parent.resolve() / "tests").exists()
        else []
    ),
]

# ── Shard selection ──────────────────────────────────────────────────────────
shard_index = int(os.environ.get("DJANGO_TEST_SHARD", "0"))
shard_count = int(os.environ.get("DJANGO_TEST_SHARDS", "1"))

apps_for_shard = [app for i, app in enumerate(test_apps) if i % shard_count == shard_index]

if not apps_for_shard:
    print(f"Shard {shard_index}/{shard_count}: no apps to run.")
    sys.exit(0)

print(
    f"Shard {shard_index + 1}/{shard_count}: "
    f"running {len(apps_for_shard)} apps: {', '.join(apps_for_shard[:5])}"
    + (f"… (+{len(apps_for_shard) - 5} more)" if len(apps_for_shard) > 5 else "")
)

# ── Single runtests.py call with --parallel ──────────────────────────────────
# Pass all shard apps in one invocation: one Django startup, --parallel
# distributes test cases across vCPUs.
runtests = pathlib.Path(__file__).parent.resolve() / "runtests.py"

cmd = [
    sys.executable,
    str(runtests),
    "--settings",
    "zodb_settings",
    "--parallel",
    "-v",
    "2",
    *apps_for_shard,
]

result = subprocess.run(cmd)  # noqa: S603
sys.exit(result.returncode)
