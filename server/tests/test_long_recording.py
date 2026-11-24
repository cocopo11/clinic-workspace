"""Long recordings remain one transcript while each existing PC input stays bounded."""
import base64
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from ai_services import AIUnavailable
from app import settings_from_env
from cloud_ai import CloudAI,inference_audio_parts
from recording import validate_audio


def audio(path,seconds,rate=8000):
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate)
        for _ in range(seconds):f.writeframesraw(b'\x01\x00'*rate)


class LongRecordingTests(unittest.TestCase):
    def test_twenty_nine_minutes_are_accepted_but_over_thirty_are_rejected(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.wav';audio(path,1740,1000)
            self.assertAlmostEqual(validate_audio(path),1740)
            audio(path,1801,1000)
            with self.assertRaises(HTTPException) as cm:validate_audio(path)
            self.assertEqual(cm.exception.status_code,413)

    def test_split_preserves_samples_offsets_and_cleans_temporary_parts(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.wav';audio(path,621)
            with inference_audio_parts(path) as parts:
                self.assertEqual([offset for _,offset in parts],[0,280,560])
                self.assertAlmostEqual(sum(validate_audio(p) for p,_ in parts),621)
                self.assertTrue(all(p.stat().st_size<25*1024*1024 for p,_ in parts))
                self.assertTrue(all(validate_audio(p)<=280 for p,_ in parts))
                saved=[p for p,_ in parts]
            self.assertTrue(path.exists());self.assertTrue(all(not p.exists() for p in saved))

    def test_short_audio_keeps_original_file_and_quality(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.wav';audio(path,10)
            with inference_audio_parts(path) as parts:self.assertEqual(parts,[(path,0.0)])
            self.assertTrue(path.exists())

    def test_existing_pc_receives_short_parts_and_returns_one_timed_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.wav';audio(path,621)
            ai=CloudAI(settings_from_env(),endpoint='https://example.test',secret='fixture')
            incoming={};finished=[]
            async def request(method,args):
                if method=='audio_begin':
                    aid=str(len(incoming));incoming[aid]=bytearray();return {'id':aid}
                if method=='audio_part':
                    self.assertEqual(args['offset'],len(incoming[args['id']]))
                    incoming[args['id']].extend(base64.b64decode(args['data']));return {}
                if method=='audio_finish':
                    raw=incoming[args['id']];target=Path(d)/('input-'+args['id']+'.wav');target.write_bytes(raw)
                    self.assertLessEqual(validate_audio(target),280);finished.append(args['id'])
                    return {'text':'구간 '+args['id'],'segments':[{'start':1,'end':2,'text':'구간'}],'language':'ko'}
                return {'ok':True}
            ai.request=request;result=ai.transcribe(path)
            self.assertEqual(len(finished),3);self.assertEqual(result['text'],'구간 0\n\n구간 1\n\n구간 2')
            self.assertEqual([s['start'] for s in result['segments']],[1,281,561])

    def test_failed_part_never_returns_partial_success_and_removes_files(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.wav';audio(path,321);paths=[]
            real=inference_audio_parts
            from contextlib import contextmanager
            @contextmanager
            def tracking(p):
                with real(p) as parts:
                    paths.extend(p for p,_ in parts);yield parts
            ai=CloudAI(settings_from_env(),endpoint='https://example.test',secret='fixture')
            async def fail(*args,**kwargs):raise AIUnavailable('fixture offline')
            ai.request=fail
            with patch('cloud_ai.inference_audio_parts',tracking),self.assertRaises(AIUnavailable):ai.transcribe(path)
            self.assertTrue(paths);self.assertTrue(all(not p.exists() for p in paths))


if __name__=='__main__':unittest.main()
