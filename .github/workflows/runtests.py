#!/usr/bin/env python
"""
Test runner for Django's test suite against the ZODB backend.

Mirrors the pattern from django-mongodb-backend's runtests.py.
Tests that are known to be unsupported are tracked in DatabaseFeatures.django_test_skips
and will be reported as skipped rather than failed.
"""
import os
import pathlib
import sys

# The initial subset of Django's test apps we aim to pass.
# Gradually expand this list as the backend matures.
test_apps = [
    # Core ORM basics — the first milestone.
    "basic",
    "bulk_create",
    "custom_pk",
    "datatypes",
    "delete",
    "empty",
    "empty_models",
    "field_defaults",
    "force_insert_update",
    "get_earliest_or_latest",
    "get_or_create",
    "lookup",
    "null_queries",
    "one_to_one",
    "or_lookups",
    "ordering",
    "save_delete_hooks",
    "update",
    "update_only_fields",
    # Relations.
    "many_to_many",
    "many_to_one",
    "many_to_one_null",
    "m2m_and_m2o",
    "m2m_through",
    "null_fk",
    # Model infrastructure.
    "model_fields",
    "model_options",
    "model_regress",
    "model_utils",
    "managers_regress",
    # Queries.
    "queries",
    "annotations",
    "expressions",
    "aggregation",
    # Forms / validation (no DB, but exercises model_to_dict etc.).
    "model_forms",
    "validation",
    # Auth (exercises the full contrib.auth workflow).
    "auth_tests",
    # Migrations.
    "migrations",
    # Add directories in django_zodb_backend/tests (if any).
    *sorted(
        [
            x.name
            for x in (pathlib.Path(__file__).parent.parent.parent.resolve() / "tests").iterdir()
            if x.is_dir() and x.name != "__pycache__"
        ]
        if (pathlib.Path(__file__).parent.parent.parent.resolve() / "tests").exists()
        else []
    ),
]

runtests = pathlib.Path(__file__).parent.resolve() / "runtests.py"
run_tests_cmd = f"python3 {runtests} %s --settings zodb_settings -v 2"

shouldFail = False
for app_name in test_apps:
    res = os.system(run_tests_cmd % app_name)  # noqa: S605
    if res != 0:
        shouldFail = True

sys.exit(1 if shouldFail else 0)
