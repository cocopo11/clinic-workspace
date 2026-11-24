"""Download models once, then reuse local inference without a paid API key."""
import asyncio
import json

import httpx
from faster_whisper import WhisperModel

from app import settings_from_env

cfg = settings_from_env()

async def main():
    async with httpx.AsyncClient(timeout=1800, trust_env=False) as c:
        try:
            r = await c.get(cfg["ollama_url"].rstrip("/")+"/api/tags")
            r.raise_for_status()
        except Exception:
            raise SystemExit("Ollama를 먼저 설치·실행하세요: https://ollama.com/download/windows")
        for model in (cfg["embedding_model"], cfg["chat_model"]):
            print(f"모델 준비: {model}", flush=True)
            last = ""
            async with c.stream("POST", cfg["ollama_url"].rstrip("/")+"/api/pull", json={"model": model, "stream": True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    if item.get("error"):
                        raise SystemExit(item["error"])
                    status = item.get("status", "")
                    if status != last:
                        print(status, flush=True)
                        last = status
    print("Whisper 모델 다운로드·로딩:", cfg["whisper_model"], flush=True)
    WhisperModel(cfg["whisper_model"], device=cfg["whisper_device"],
                 compute_type=cfg["whisper_compute"], download_root=cfg["model_cache"])
    print("모델 준비 완료. 서버를 실행하고 관리자로 로그인한 뒤 자료 색인을 생성하세요.")

if __name__ == "__main__":
    asyncio.run(main())
