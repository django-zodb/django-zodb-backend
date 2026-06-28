"""
Test ZODB-specific backend behaviours.
"""

import django
from django.conf import settings


def configure_django():
    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django_zodb_backend",
                    "NAME": "test",
                    "OPTIONS": {"storage": "memory"},
                }
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
            USE_TZ=False,
        )
        django.setup()


configure_django()


from django.db import connection  # noqa: E402


class TestConnectionBasics:
    """Verify the DatabaseWrapper connects and basic operations work."""

    def setup_method(self):
        connection.switch_to_test_storage()

    def teardown_method(self):
        connection.close_test_storage()

    def test_zodb_root_accessible(self):
        root = connection.zodb_root
        assert root is not None

    def test_ensure_collection_creates_btree(self):
        from BTrees.LOBTree import LOBTree

        coll = connection.ensure_collection("test_model")
        assert isinstance(coll, LOBTree)

    def test_get_collection_returns_none_for_missing(self):
        result = connection.get_collection("nonexistent")
        assert result is None

    def test_get_next_pk_increments(self):
        pk1 = connection.get_next_pk("test_model")
        pk2 = connection.get_next_pk("test_model")
        pk3 = connection.get_next_pk("test_model")
        assert pk1 == 1
        assert pk2 == 2
        assert pk3 == 3

    def test_get_next_pk_separate_tables(self):
        pk_a = connection.get_next_pk("table_a")
        pk_b = connection.get_next_pk("table_b")
        pk_a2 = connection.get_next_pk("table_a")
        assert pk_a == 1
        assert pk_b == 1
        assert pk_a2 == 2

    def test_drop_collection(self):
        connection.ensure_collection("drop_me")
        assert connection.get_collection("drop_me") is not None
        connection.drop_collection("drop_me")
        assert connection.get_collection("drop_me") is None

    def test_insert_and_retrieve(self):
        import transaction
        from persistent.mapping import PersistentMapping

        coll = connection.ensure_collection("mymodel")
        pk = connection.get_next_pk("mymodel")
        coll[pk] = PersistentMapping({"id": pk, "name": "Alice", "age": 30})
        transaction.commit()

        retrieved = coll[pk]
        assert retrieved["name"] == "Alice"
        assert retrieved["age"] == 30

    def test_in_memory_storage_isolated_between_tests(self):
        """Each test_method gets a fresh in-memory ZODB via setup_method."""
        import transaction
        from persistent.mapping import PersistentMapping

        coll = connection.ensure_collection("isolation_test")
        coll[1] = PersistentMapping({"id": 1, "value": "first test"})
        transaction.commit()
        # This data should not appear in the next test.

    def test_isolation_from_previous_test(self):
        """If isolation works, the previous test's data should not be here."""
        coll = connection.get_collection("isolation_test")
        assert coll is None  # fresh storage — collection doesn't exist


class TestVendorInfo:
    def test_vendor(self):
        assert connection.vendor == "zodb"

    def test_display_name(self):
        assert connection.display_name == "ZODB"
