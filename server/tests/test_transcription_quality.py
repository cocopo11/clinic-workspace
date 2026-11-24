import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ai_services import LocalAI
from transcription_quality import korean_hints
from upgrade_transcription import upgrade


class TranscriptionQualityTests(unittest.TestCase):
    def test_hints_are_bounded_korean_only_and_optional(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'terms.txt';p.write_text('# comment\n녹음\n빈혈\n녹음\n'+'가'*51+'\n',encoding='utf-8')
            hints=korean_hints({'whisper_terms_file':str(p)},'ko')
            self.assertEqual(hints['hotwords'],'녹음, 빈혈')
            self.assertEqual(korean_hints({},'en'),{})
            self.assertEqual(korean_hints({},'auto'),{})
            self.assertNotIn('hotwords',korean_hints({'whisper_terms_file':str(Path(d)/'missing')},'ko'))

    def test_shared_transcriber_passes_hints_without_replacing_recognized_text(self):
        cfg={'ollama_url':'http://localhost','embedding_model':'test','chat_model':'test','whisper_model':'small','whisper_device':'cpu','whisper_compute':'int8','model_cache':'test'}
        ai=LocalAI(cfg);captured={}
        class Model:
            def transcribe(self,path,**options):
                captured.update(options)
                return iter([SimpleNamespace(start=0,end=1,text='노금과 빈열')]),SimpleNamespace(duration=1,language='ko',language_probability=.9)
        ai._whisper=Model()
        with patch.dict('sys.modules',faster_whisper=SimpleNamespace(WhisperModel=Model)):
            result=ai.transcribe('test.wav','ko')
        self.assertIn('녹음',captured['hotwords']);self.assertIn('빈혈',captured['hotwords'])
        self.assertEqual(result['text'],'노금과 빈열')
        self.assertEqual(captured['language'],'ko');self.assertTrue(captured['vad_filter'])

    def test_upgrade_preserves_unrelated_settings_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';original=b'{"chat_model":"existing","whisper_model":"small","public_base_url":"https://local.test"}';p.write_bytes(original)
            seen=[]
            upgrade(d,lambda model,**kw:seen.append((model,kw)))
            self.assertEqual(json.loads(p.read_text())['whisper_model'],'large-v3')
            self.assertEqual(json.loads(p.read_text())['chat_model'],'existing')
            self.assertEqual((Path(d)/'config.before-transcription-upgrade.json').read_bytes(),original)
            self.assertEqual(seen[0][0],'large-v3')

    def test_failed_download_does_not_change_existing_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text('{"whisper_model":"small"}')
            def fail(*args,**kw):raise RuntimeError('test download failed')
            with self.assertRaises(RuntimeError):upgrade(d,fail)
            self.assertEqual(p.read_text(),'{"whisper_model":"small"}')

    def test_config_change_during_download_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text('{}')
            def changed(*args,**kw):p.write_text('{"chat_model":"changed"}')
            with self.assertRaises(RuntimeError):upgrade(d,changed)
            self.assertEqual(p.read_text(),'{"chat_model":"changed"}')
