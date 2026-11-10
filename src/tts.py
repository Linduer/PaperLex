"""用 edge-tts 生成发音，结果缓存到 cache/audio。"""
import asyncio
import hashlib
import os

import edge_tts

from src.config import AUDIO_DIR, TTS_VOICE


def _cache_path(text, voice=TTS_VOICE):
    digest = hashlib.md5(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(AUDIO_DIR, f"{digest}.mp3")


async def _synth(text, path, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(path)


def ensure_audio(text, voice=TTS_VOICE):
    """返回 mp3 路径，已存在则直接复用。失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    os.makedirs(AUDIO_DIR, exist_ok=True)
    path = _cache_path(text, voice)
    if os.path.exists(path) and os.path.getsize(path) > 512:
        return path
    try:
        asyncio.run(_synth(text, path, voice))
    except Exception as exc:  # 网络抖动或服务限流
        print(f"  [发音失败] {text}: {exc}")
        return None
    if os.path.exists(path) and os.path.getsize(path) > 512:
        return path
    return None


def ensure_audio_batch(texts, voice=TTS_VOICE):
    """批量合成，返回 {文本: 路径}。并发度 4，避免被限流。"""
    results = {}
    pending = [t for t in dict.fromkeys(texts) if t and t.strip()]

    # 先收割缓存
    todo = []
    for text in pending:
        path = _cache_path(text, voice)
        if os.path.exists(path) and os.path.getsize(path) > 512:
            results[text] = path
        else:
            todo.append(text)

    if not todo:
        return results

    async def worker():
        sem = asyncio.Semaphore(4)

        async def one(text):
            async with sem:
                path = _cache_path(text, voice)
                try:
                    await _synth(text, path, voice)
                    if os.path.getsize(path) > 512:
                        results[text] = path
                except Exception as exc:
                    print(f"  [发音失败] {text}: {exc}")

        await asyncio.gather(*(one(t) for t in todo))

    asyncio.run(worker())
    print(f"  发音：复用 {len(pending) - len(todo)} 个，新生成 {len(todo)} 个")
    return results
