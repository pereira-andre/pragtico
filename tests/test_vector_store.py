import sys
import types
import unittest
from unittest.mock import patch

from integrations.vector_store import PgvectorIndexStore


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append((sql, params))
        if self.connection.execute_error is not None:
            raise self.connection.execute_error


class _FakeConnection:
    def __init__(self, *, execute_error=None):
        self.execute_error = execute_error
        self.executed = []
        self.committed = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class PgvectorIndexStoreTests(unittest.TestCase):
    def _patch_driver_modules(self, *, connections, register_calls):
        connection_queue = list(connections)

        def _connect(*args, **kwargs):
            self.assertTrue(connection_queue)
            return connection_queue.pop(0)

        def _register_vector(connection):
            register_calls.append(connection)

        psycopg_module = types.ModuleType("psycopg")
        psycopg_module.connect = _connect
        psycopg_rows_module = types.ModuleType("psycopg.rows")
        psycopg_rows_module.dict_row = object()
        pgvector_root_module = types.ModuleType("pgvector")
        pgvector_module = types.ModuleType("pgvector.psycopg")
        pgvector_module.register_vector = _register_vector

        return patch.dict(
            sys.modules,
            {
                "psycopg": psycopg_module,
                "psycopg.rows": psycopg_rows_module,
                "pgvector": pgvector_root_module,
                "pgvector.psycopg": pgvector_module,
            },
        )

    def test_schema_bootstrap_runs_before_registering_vector_type(self):
        schema_connection = _FakeConnection()
        query_connection = _FakeConnection()
        register_calls = []

        with self._patch_driver_modules(
            connections=[schema_connection, query_connection],
            register_calls=register_calls,
        ):
            store = PgvectorIndexStore(database_url="postgres://example")
            self.assertTrue(schema_connection.committed)
            self.assertTrue(schema_connection.executed)
            self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", schema_connection.executed[0][0])
            self.assertEqual(register_calls, [])

            with store._connect() as conn:
                self.assertIs(conn, query_connection)

        self.assertEqual(register_calls, [query_connection])

    def test_missing_pgvector_extension_raises_clear_runtime_error(self):
        schema_error = RuntimeError('extension "vector" is not available')
        register_calls = []

        with self._patch_driver_modules(
            connections=[_FakeConnection(execute_error=schema_error)],
            register_calls=register_calls,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                PgvectorIndexStore(database_url="postgres://example")

        self.assertIn("não tem a extensão pgvector disponível", str(ctx.exception).lower())
        self.assertEqual(register_calls, [])


if __name__ == "__main__":
    unittest.main()
