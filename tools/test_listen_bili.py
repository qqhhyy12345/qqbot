"""端到端测试：下载一个 B 站视频的音频 -> 跑 worker -> 打印结果并确认音频已删。"""
import asyncio, json, sys, time
sys.path.insert(0, r"D:/qqbot/astrbot/data/plugins/astrbot_plugin_listen_bili/core")
from bili_client import MiniBili, UA  # noqa

import io as _io, json as _json
_COOKIE = _json.load(_io.open(r"D:/qqbot/astrbot/data/config/astrbot_plugin_bilibili_config.json", encoding="utf-8-sig")).get("cookie", "")

async def main():
    import aiohttp
    client = MiniBili(cookie=_COOKIE)
    async with aiohttp.ClientSession(trust_env=False) as session:
        await client.init_wbi(session)
        if len(sys.argv) > 1 and sys.argv[1].strip():
            bvid = sys.argv[1]
            url, title, dur = await client.get_audio_url(session, bvid)
        else:
            kw = sys.argv[2] if len(sys.argv) > 2 else "科普"
            res = await client.search(session, kw, 10)
            def _dur(v):
                d = str(v.get("duration", "0"))
                parts = [int(x) for x in d.split(":") if x.isdigit()]
                while len(parts) < 3:
                    parts.insert(0, 0)
                return parts[0] * 3600 + parts[1] * 60 + parts[2]

            vids = [v for v in res
                    if v.get("type", 0) == "video" and 60 <= _dur(v) <= 700]
            if not vids:
                raise RuntimeError("没搜到合适的视频")
            bvid = vids[0]["bvid"]
        url, title, dur = await client.get_audio_url(session, bvid)
        print("title:", title, "| dur:", dur, "s")
        import tempfile, pathlib
        dest = pathlib.Path(tempfile.gettempdir()) / "bili_test_audio.m4s"
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}) as resp:
            dest.write_bytes(await resp.read())
        print("audio size:", dest.stat().st_size)
    t = time.time()
    import subprocess
    WORKER_PY = r"C:/Users/ximen/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
    WORKER = r"D:/qqbot/astrbot/data/plugins/astrbot_plugin_listen_bili/core/audio_worker.py"
    job = json.dumps({"audio_path": str(dest), "max_minutes": 2})
    p = subprocess.run([WORKER_PY, WORKER, r"D:/qqbot/models/faster-whisper-small", "cpu"],
                       input=job.encode(), capture_output=True, timeout=300)
    print("worker %.1fs rc=%d" % (time.time() - t, p.returncode))
    print("RAW STDOUT:", p.stdout.decode("utf-8", "ignore")[:800])
    print("STDERR tail:", p.stderr.decode("utf-8", "ignore")[-500:])
    out = p.stdout.decode("utf-8", "ignore").strip().splitlines()
    if out:
        print("RESULT:", out[-1][:1200])
    print("audio still exists:", dest.exists())

asyncio.run(main())
