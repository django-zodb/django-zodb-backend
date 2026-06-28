import re
import uuid

from django.db.backends.base.operations import BaseDatabaseOperations
from django.utils.regex_helper import _lazy_re_compile


class DatabaseOperations(BaseDatabaseOperations):
    """
    ZODB-specific database operations.

    ZODB stores Python objects directly, so many SQL-centric methods are
    no-ops. The key responsibility here is value adaptation (Python ↔ storage
    representation) and providing SQL template strings that Django's internal
    machinery needs even when the backend never actually executes SQL.
    """

    compiler_module = "django_zodb_backend.compiler"

    # ZODB stores Python objects natively — no type coercion on write.
    # adapt_* methods convert Django field values to what ZODB will store.

    def adapt_datefield_value(self, value):
        return value  # store datetime.date as-is

    def adapt_datetimefield_value(self, value):
        return value  # store datetime.datetime as-is

    def adapt_timefield_value(self, value):
        return value  # store datetime.time as-is

    def adapt_decimalfield_value(self, value, max_digits=None, decimal_places=None):
        return value  # store decimal.Decimal as-is

    def adapt_unknown_value(self, value):
        return value

    # -------------------------------------------------------------------------
    # Django internals expect these SQL template helpers even if the backend
    # never executes SQL. They are used during query construction before the
    # compiler routes to ZODB.
    # -------------------------------------------------------------------------

    _extract_format_re = _lazy_re_compile(r"[A-Z_]+")

    def date_extract_sql(self, lookup_type, sql, params):
        if lookup_type == "week_day":
            return f"EXTRACT(DOW FROM {sql}) + 1", params
        if lookup_type == "iso_week_day":
            return f"EXTRACT(ISODOW FROM {sql})", params
        if lookup_type == "iso_year":
            return f"EXTRACT(ISOYEAR FROM {sql})", params
        lookup_type = lookup_type.upper()
        if not self._extract_format_re.fullmatch(lookup_type):
            raise ValueError(f"Invalid lookup type: {lookup_type!r}")
        return f"EXTRACT({lookup_type} FROM {sql})", params

    def datetime_extract_sql(self, lookup_type, sql, params, tzname):
        if lookup_type == "second":
            return f"EXTRACT(SECOND FROM DATE_TRUNC(%s, {sql}))", ("second", *params)
        return self.date_extract_sql(lookup_type, sql, params)

    def datetime_trunc_sql(self, lookup_type, sql, params, tzname):
        return f"DATE_TRUNC(%s, {sql})", (lookup_type, *params)

    def date_trunc_sql(self, lookup_type, sql, params, tzname=None):
        return f"DATE_TRUNC(%s, {sql})", (lookup_type, *params)

    def datetime_cast_date_sql(self, sql, params, tzname):
        return f"({sql})::date", params

    def datetime_cast_time_sql(self, sql, params, tzname):
        return f"({sql})::time", params

    def time_trunc_sql(self, lookup_type, sql, params, tzname=None):
        return f"DATE_TRUNC(%s, {sql})::time", (lookup_type, *params)

    def format_for_duration_arithmetic(self, sql):
        return f"INTERVAL {sql} MILLISECOND"

    def quote_name(self, name):
        # No SQL quoting needed — BTree table names are plain Python strings.
        if name.startswith('"') and name.endswith('"'):
            return name
        return name

    def prep_for_like_query(self, x):
        return re.escape(str(x))

    def sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
        # Return table names — execute_sql_flush will clear each BTree.
        return tables

    def execute_sql_flush(self, tables):
        for table in tables:
            if table.startswith("system."):
                continue
            coll = self.connection.get_btree(table)
            if coll is not None:
                coll.clear()
                import transaction

                transaction.commit()

    def get_db_converters(self, expression):
        converters = super().get_db_converters(expression)
        internal_type = expression.output_field.get_internal_type()
        if internal_type == "UUIDField":
            converters.append(self.convert_uuidfield_value)
        return converters

    def convert_uuidfield_value(self, value, expression, connection):
        if value is not None and not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value

    def integer_field_range(self, internal_type):
        # Match Django's standard ranges; ZODB doesn't enforce them but
        # OOBTree supports any hashable key (int, str, etc.).
        ranges = {
            "SmallIntegerField": (-32768, 32767),
            "PositiveSmallIntegerField": (0, 32767),
            "IntegerField": (-2147483648, 2147483647),
            "PositiveIntegerField": (0, 2147483647),
            "BigIntegerField": (-9223372036854775808, 9223372036854775807),
            "PositiveBigIntegerField": (0, 9223372036854775807),
        }
        return ranges.get(internal_type, (-9223372036854775808, 9223372036854775807))

    def last_insert_id(self, cursor, table_name, pk_name):
        # The compiler sets the PK before saving; return the last-used PK
        # from the connection's per-table counter.
        return self.connection.get_last_insert_id(table_name)

    def no_limit_value(self):
        return None

    def limit_offset_sql(self, low_mark, high_mark):
        # Not used — the compiler handles slicing in Python.
        return ""
