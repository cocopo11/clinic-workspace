"""Read concurrency must preserve the exclusive lock for business writes."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import postgres_store

class LockTests(unittest.TestCase):
 def test_read_lock_is_shared_while_default_write_transactions_still_exclude_readers(self):
  sql=[]
  class Raw:
   def execute(self,text):sql.append(text)
   @contextmanager
   def transaction(self):yield
  class Pool:
   @contextmanager
   def connection(self):yield Raw()
  with patch.dict(postgres_store._pools,{'fixture-lock':Pool()},clear=True):
   with postgres_store.connect('fixture-lock',shared=True):pass
   with postgres_store.connect('fixture-lock'):pass
  self.assertEqual(sql,['SELECT pg_advisory_xact_lock_shared(71117002)','SELECT pg_advisory_xact_lock(71117002)'])

if __name__=='__main__':unittest.main()
