"""A disconnected database must not become an unrelated attribute error."""
import sqlite3
import unittest
from postgres_store import Connection


class DatabaseErrors(unittest.TestCase):
    def test_network_error_without_sqlstate_preserves_the_original_exception(self):
        class LostConnection(RuntimeError):sqlstate=None
        original=LostConnection('connection lost')
        class Raw:
            def execute(self,*args):raise original
        with self.assertRaises(LostConnection) as caught:Connection(Raw()).execute('SELECT 1')
        self.assertIs(caught.exception,original)

    def test_constraint_errors_keep_the_existing_duplicate_handling(self):
        class Duplicate(RuntimeError):sqlstate='23505'
        original=Duplicate('duplicate')
        class Raw:
            def execute(self,*args):raise original
        with self.assertRaises(sqlite3.IntegrityError) as caught:Connection(Raw()).execute('INSERT INTO users(id) VALUES(?)',('fixture',))
        self.assertIs(caught.exception.__cause__,original)
