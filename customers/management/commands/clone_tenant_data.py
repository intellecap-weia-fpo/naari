from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django_tenants.utils import get_public_schema_name

from customers.models import Client, Domain


EXCLUDED_TABLES = {"django_migrations"}
EXCLUDED_PREFIXES = ("customers_",)
ROW_COUNT_TABLES = (
    "auth_user",
    "management_userprofile",
    "neo_siteconfiguration",
    "survey_survey",
    "survey_submission",
)


def quote_name(name):
    return connection.ops.quote_name(name)


class Command(BaseCommand):
    help = "Clone tenant data from one schema to another while preserving primary keys."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Source tenant schema, for example: weai")
        parser.add_argument("--target", required=True, help="Target tenant schema, for example: tenant2")
        parser.add_argument("--name", required=True, help="Target tenant display name")
        parser.add_argument("--domain", required=True, help="Target tenant primary domain, for example: tenant2.localhost")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Allow replacing data in an existing target tenant schema.",
        )
        parser.add_argument(
            "--skip-migrate",
            action="store_true",
            help="Skip migrate_schemas for the target schema before copying.",
        )

    def handle(self, *args, **options):
        source = options["source"]
        target = options["target"]
        name = options["name"]
        domain = options["domain"]
        replace = options["replace"]

        if source == get_public_schema_name():
            raise CommandError("Refusing to clone from public. Use a tenant schema as --source.")
        if target == get_public_schema_name():
            raise CommandError("Refusing to clone into public.")
        if source == target:
            raise CommandError("--source and --target must be different schemas.")

        self._ensure_schema_exists(source)

        tenant, created = Client.objects.get_or_create(
            schema_name=target,
            defaults={"name": name, "is_active": True},
        )
        if not created:
            tenant.name = name
            tenant.is_active = True
            tenant.save(update_fields=["name", "is_active"])

        Domain.objects.update_or_create(
            domain=domain,
            defaults={"tenant": tenant, "is_primary": True},
        )

        if not options["skip_migrate"]:
            call_command("migrate_schemas", schema_name=target, interactive=False, verbosity=options["verbosity"])

        if not replace and self._schema_has_data(target):
            raise CommandError(
                f"Target schema '{target}' already has data. Re-run with --replace to overwrite tenant data."
            )

        tables = self._copyable_tables(source, target)
        if not tables:
            raise CommandError("No matching tenant tables found to clone.")

        copied = self._clone_tables(source, target, tables)
        counts = self._row_counts(source, target)

        self.stdout.write(self.style.SUCCESS(f"Cloned {source} -> {target}."))
        self.stdout.write(f"Copied tables: {len(copied)}")
        for table, source_count, target_count in counts:
            self.stdout.write(f"{table}: {source_count} -> {target_count}")
        self.stdout.write(self.style.SUCCESS(f"Domain ready: {domain}"))

    def _ensure_schema_exists(self, schema_name):
        with connection.cursor() as cursor:
            cursor.execute(
                "select exists(select 1 from information_schema.schemata where schema_name = %s)",
                [schema_name],
            )
            if not cursor.fetchone()[0]:
                raise CommandError(f"Schema '{schema_name}' does not exist.")

    def _schema_has_data(self, schema_name):
        with connection.cursor() as cursor:
            for table in ROW_COUNT_TABLES:
                if self._table_exists(schema_name, table, cursor=cursor):
                    cursor.execute(f"select count(*) from {quote_name(schema_name)}.{quote_name(table)}")
                    if cursor.fetchone()[0] > 0:
                        return True
        return False

    def _table_exists(self, schema_name, table_name, cursor=None):
        owns_cursor = cursor is None
        if owns_cursor:
            cursor = connection.cursor()
        try:
            cursor.execute(
                """
                select exists(
                    select 1
                    from information_schema.tables
                    where table_schema = %s and table_name = %s and table_type = 'BASE TABLE'
                )
                """,
                [schema_name, table_name],
            )
            return cursor.fetchone()[0]
        finally:
            if owns_cursor:
                cursor.close()

    def _copyable_tables(self, source, target):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select table_name
                from information_schema.tables
                where table_schema = %s and table_type = 'BASE TABLE'
                intersect
                select table_name
                from information_schema.tables
                where table_schema = %s and table_type = 'BASE TABLE'
                order by table_name
                """,
                [source, target],
            )
            return [
                row[0]
                for row in cursor.fetchall()
                if row[0] not in EXCLUDED_TABLES
                and not any(row[0].startswith(prefix) for prefix in EXCLUDED_PREFIXES)
            ]

    def _shared_columns(self, source, target, table):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = %s and table_name = %s
                intersect
                select column_name
                from information_schema.columns
                where table_schema = %s and table_name = %s
                order by column_name
                """,
                [source, table, target, table],
            )
            return [row[0] for row in cursor.fetchall()]

    def _clone_tables(self, source, target, tables):
        copied = []
        with connection.cursor() as cursor:
            with transaction.atomic():
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                cursor.execute(
                    "TRUNCATE "
                    + ", ".join(f"{quote_name(target)}.{quote_name(table)}" for table in tables)
                    + " CASCADE"
                )

                for table in tables:
                    columns = self._shared_columns(source, target, table)
                    if not columns:
                        continue
                    column_sql = ", ".join(quote_name(column) for column in columns)
                    cursor.execute(
                        f"""
                        INSERT INTO {quote_name(target)}.{quote_name(table)} ({column_sql})
                        OVERRIDING SYSTEM VALUE
                        SELECT {column_sql}
                        FROM {quote_name(source)}.{quote_name(table)}
                        """
                    )
                    copied.append(table)

                self._remove_orphans(cursor, target)
                self._reset_sequences(cursor, target, tables)
        return copied

    def _remove_orphans(self, cursor, schema_name):
        cursor.execute(
            """
            select child.relname, child_col.attname, parent.relname, parent_col.attname
            from pg_constraint con
            join pg_class child on child.oid = con.conrelid
            join pg_namespace child_ns on child_ns.oid = child.relnamespace
            join pg_class parent on parent.oid = con.confrelid
            join pg_namespace parent_ns on parent_ns.oid = parent.relnamespace
            join pg_attribute child_col on child_col.attrelid = child.oid and child_col.attnum = con.conkey[1]
            join pg_attribute parent_col on parent_col.attrelid = parent.oid and parent_col.attnum = con.confkey[1]
            where con.contype = 'f'
              and child_ns.nspname = %s
              and parent_ns.nspname = %s
              and array_length(con.conkey, 1) = 1
            """,
            [schema_name, schema_name],
        )
        fks = cursor.fetchall()
        total_removed = 0
        for _ in range(8):
            removed_this_pass = 0
            for child, child_col, parent, parent_col in fks:
                cursor.execute(
                    f"""
                    delete from {quote_name(schema_name)}.{quote_name(child)} child_row
                    where child_row.{quote_name(child_col)} is not null
                      and not exists (
                        select 1
                        from {quote_name(schema_name)}.{quote_name(parent)} parent_row
                        where parent_row.{quote_name(parent_col)} = child_row.{quote_name(child_col)}
                      )
                    """
                )
                removed_this_pass += cursor.rowcount
            total_removed += removed_this_pass
            if removed_this_pass == 0:
                break
        if total_removed:
            self.stdout.write(f"Removed orphan rows in target schema: {total_removed}")

    def _reset_sequences(self, cursor, schema_name, tables):
        for table in tables:
            cursor.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = %s and table_name = %s and column_default like 'nextval%%'
                """,
                [schema_name, table],
            )
            for (column,) in cursor.fetchall():
                cursor.execute("select pg_get_serial_sequence(%s, %s)", [f"{schema_name}.{table}", column])
                sequence_name = cursor.fetchone()[0]
                if sequence_name:
                    cursor.execute(
                        f"""
                        select setval(
                            %s,
                            coalesce((select max({quote_name(column)}) from {quote_name(schema_name)}.{quote_name(table)}), 1),
                            (select count(*) > 0 from {quote_name(schema_name)}.{quote_name(table)})
                        )
                        """,
                        [sequence_name],
                    )

    def _row_counts(self, source, target):
        counts = []
        with connection.cursor() as cursor:
            for table in ROW_COUNT_TABLES:
                if self._table_exists(source, table, cursor=cursor) and self._table_exists(target, table, cursor=cursor):
                    cursor.execute(f"select count(*) from {quote_name(source)}.{quote_name(table)}")
                    source_count = cursor.fetchone()[0]
                    cursor.execute(f"select count(*) from {quote_name(target)}.{quote_name(table)}")
                    target_count = cursor.fetchone()[0]
                    counts.append((table, source_count, target_count))
        return counts
