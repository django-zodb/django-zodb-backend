from django.db.backends.base.client import BaseDatabaseClient


class DatabaseClient(BaseDatabaseClient):
    executable_name = "zodb-shell"

    def runshell(self, parameters):
        raise NotImplementedError("Interactive ZODB shell not yet implemented.")
