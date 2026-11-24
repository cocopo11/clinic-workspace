"""One-time, atomic upgrade; configuration changes only after the model loads."""
import json
import os
import tempfile
from pathlib import Path


def upgrade(directory=None,model_factory=None):
    root=Path(directory) if directory else Path(__file__).resolve().parent
    config_path=root/'config.json'
    original=config_path.read_bytes() if config_path.exists() else None
    cfg=json.loads(original.decode('utf-8-sig')) if original else {}
    cache=Path(cfg.get('model_cache',root/'models'))
    if not cache.is_absolute():cache=root/cache
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory=WhisperModel
    print('large-v3 모델 다운로드·로딩 중입니다. 첫 실행은 시간이 걸릴 수 있습니다.',flush=True)
    model=model_factory('large-v3',device=cfg.get('whisper_device','cpu'),compute_type=cfg.get('whisper_compute','int8'),download_root=str(cache))
    # Verify that existing local configuration was not edited during the download.
    if (config_path.read_bytes() if config_path.exists() else None)!=original:
        raise RuntimeError('설정 파일이 변경됐습니다. 서버를 종료하고 다시 실행하세요.')
    if original is not None:(root/'config.before-transcription-upgrade.json').write_bytes(original)
    cfg['whisper_model']='large-v3'
    fd,name=tempfile.mkstemp(prefix='transcription-',suffix='.tmp',dir=root)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(cfg,f,ensure_ascii=False,indent=2)
        os.replace(name,config_path)
    finally:Path(name).unlink(missing_ok=True)
    print('large-v3 설정 완료. 기존 방식으로 서버를 다시 시작하세요. CPU에서는 전사가 느릴 수 있습니다.',flush=True)
    return model

if __name__=='__main__':
    try:upgrade()
    except Exception as e:raise SystemExit('전사 모델 변경을 완료하지 못했습니다: '+str(e))
