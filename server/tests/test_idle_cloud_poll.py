import asyncio,unittest
from contextlib import contextmanager
from types import SimpleNamespace
from cloud_ai import process_cloud_job

class IdlePollTests(unittest.TestCase):
 def test_idle_poll_does_not_write_or_call_local_pc(self):
  sql=[];modes=[]
  class Connection:
   def execute(self,statement,args):sql.append(statement);return self
   def fetchone(self):return None
  class Store:
   @contextmanager
   def connect(self,*,shared=False):modes.append(shared);yield Connection()
  class AI:
   async def status(self):raise AssertionError('idle poll must not reach PC')
  jobs=SimpleNamespace(store=Store(),ai=AI())
  asyncio.run(process_cloud_job(jobs,'recording_jobs',{'id':'doctor','role':'doctor'}))
  self.assertEqual(modes,[True]);self.assertEqual(len(sql),1)
  self.assertTrue(sql[0].startswith('SELECT '));self.assertNotIn('UPDATE ',sql[0])

if __name__=='__main__':unittest.main()
