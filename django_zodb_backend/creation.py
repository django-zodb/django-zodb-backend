"""
DatabaseCreation for ZODB.

Controls how the test database is created and destroyed. Unlike SQL backends,
ZODB test isolation is achieved by switching to an in-memory MappingStorage
for each test run — no separate database file or server is needed.
"""

from django.db.backends.base.creation import BaseDatabaseCreation


class DatabaseCreation(BaseDatabaseCreation):
    def _get_test_db_name(self):
        # For ZODB we use the alias as the namespace; "test_" prefix is cosmetic.
        return f"test_{self.connection.settings_dict['NAME']}"

    def _create_test_db(self, verbosity, autoclobber, keepdb=False):
        """Switch to an in-memory MappingStorage for the test run."""
        self.connection.switch_to_test_storage()
        if verbosity >= 1:
            print("Using in-memory ZODB storage for test database.")
        return self._get_test_db_name()

    def _destroy_test_db(self, test_database_name, verbosity):
        """Close and discard the in-memory test storage."""
        self.connection.close_test_storage()

    def create_test_db(self, verbosity=1, autoclobber=False, serialize=True, keepdb=False):
        # Bypass the SQL-specific parts of BaseDatabaseCreation.create_test_db.
        test_db_name = self._create_test_db(verbosity, autoclobber, keepdb)
        self.connection.settings_dict["NAME"] = test_db_name
        self.connection.ensure_connection()
        return test_db_name

    def destroy_test_db(self, old_database_name=None, verbosity=1, keepdb=False, suffix=None):
        if not keepdb:
            self._destroy_test_db(self.connection.settings_dict["NAME"], verbosity)
        if old_database_name is not None:
            self.connection.settings_dict["NAME"] = old_database_name

    def serialize_db_to_string(self):
        """
        Django calls this to snapshot the DB before running tests with
        ``--keepdb``. Not supported for ZODB yet.
        """
        return "{}"

    def deserialize_db_from_string(self, data):
        pass
