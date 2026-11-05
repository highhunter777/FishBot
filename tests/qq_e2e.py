"""验证 OneBot 渠道：事件解析、@ 过滤、去重、发送结构。"""
from __future__ import annotations

import asyncio
import http.server
import pathlib
import shutil
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mock_onebot import MockOneBot  # noqa: E402

from app.channel.qq_onebot import OneBotChannel  # noqa: E402
from app.domain import OutboundAction  # noqa: E402
from app.files import download  # noqa: E402

PORT = 30011


async def main() -> None:
    async with MockOneBot(PORT) as server:
        media = pathlib.Path(tempfile.mkdtemp())
        voice = media / "voice.amr"
        voice.write_bytes(b"fake")

        channel = OneBotChannel(
            ws_url=f"ws://127.0.0.1:{PORT}",
            allowed_users={"12345"},
            media_root=str(media),
            transcriber=lambda path: "今天天气不错",
        )

        events: asyncio.Queue = asyncio.Queue()

        async def consume():
            async for event in channel.run():
                await events.put(await channel.prepare(event))

        task = asyncio.create_task(consume())
        await asyncio.wait_for(server.connected.wait(), timeout=5)
        await asyncio.sleep(0.3)

        # 1) 私聊
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 12345,
                "message_id": 1,
                "message": [{"type": "text", "data": {"text": "在吗"}}],
                "raw_message": "在吗",
            }
        )
        ev = await asyncio.wait_for(events.get(), timeout=3)
        assert ev.is_private and ev.text == "在吗" and ev.conv_id == "u:12345", ev
        assert ev.reply_to == "1"
        print("ok 私聊解析")

        # 2) 重复消息应被丢弃
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 12345,
                "message_id": 1,
                "message": [{"type": "text", "data": {"text": "在吗"}}],
            }
        )
        try:
            await asyncio.wait_for(events.get(), timeout=1)
            raise AssertionError("重复消息没有被丢弃")
        except TimeoutError:
            print("ok 重复消息丢弃")

        # 3) 群里没 @ 应被丢弃
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 555,
                "user_id": 12345,
                "message_id": 3,
                "message": [{"type": "text", "data": {"text": "路过"}}],
            }
        )
        try:
            await asyncio.wait_for(events.get(), timeout=1)
            raise AssertionError("未 @ 的群消息不该进来")
        except TimeoutError:
            print("ok 群里未 @ 丢弃")

        # 4) 群里 @ 了小号
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 555,
                "user_id": 12345,
                "message_id": 4,
                "message": [
                    {"type": "at", "data": {"qq": "99999"}},
                    {"type": "text", "data": {"text": " 帮我看下"}},
                ],
            }
        )
        ev = await asyncio.wait_for(events.get(), timeout=3)
        assert not ev.is_private and ev.text == "帮我看下" and ev.conv_id == "g:555", ev
        print("ok 群 @ 解析")

        # 5) 语音：record 段落到本地并转写成文字，语音-only 也要能进会话
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 12345,
                "message_id": 5,
                "message": [
                    {"type": "record", "data": {"file": "voice.amr", "path": str(voice)}}
                ],
            }
        )
        ev = await asyncio.wait_for(events.get(), timeout=3)
        assert ev.text == "[语音] 今天天气不错", ev.text
        assert ev.attachments[0]["kind"] == "voice", ev.attachments
        print("ok 语音转写")

        # 6) 本地图片转 base64 发送（NapCat 在容器里够不到宿主机路径）
        image = media / "sticker.jpg"
        image.write_bytes(b"fake-image")
        await channel.send("u:12345", OutboundAction("image", media=str(image)))
        await asyncio.sleep(0.2)
        image_msg = [r for r in server.received if r.get("action") == "send_private_msg"][-1]
        seg = image_msg["params"]["message"][0]
        assert seg["type"] == "image" and seg["data"]["file"].startswith("base64://"), seg
        print("ok 本地图片转 base64")

        # 6.5) 下载上限：超限直接丢弃，不落盘
        class BigFile(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"x" * 200_000
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), BigFile)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            big = await download(
                f"http://127.0.0.1:{httpd.server_port}/big", media, ".bin", max_bytes=1024
            )
            assert big is None, big
            assert not list(media.glob("*.bin")), "超限文件不该留在磁盘"
        finally:
            httpd.shutdown()
        print("ok 下载超限丢弃")

        # 7) 发送结构
        await channel.send("u:12345", OutboundAction("text", "收到[ CQ:at,qq=1 ]"))
        await asyncio.sleep(0.2)
        sent = [r for r in server.received if r.get("action") == "send_private_msg"]
        assert sent, server.received
        assert sent[-1]["params"]["message"] == [{"type": "text", "data": {"text": "收到[ CQ:at,qq=1 ]"}}]
        assert sent[-1]["params"]["user_id"] == 12345
        print("ok 私聊发送结构（消息段数组，天然免疫 CQ 注入）")

        await channel.send("g:555", OutboundAction("text", "群回复"))
        await asyncio.sleep(0.2)
        assert any(r.get("action") == "send_group_msg" for r in server.received)
        print("ok 群发送")

        # 7) 引用 + @ 提问者
        await channel.send(
            "g:555",
            OutboundAction("text", "带引用", at_user="12345"),
            reply_to="4",
        )
        await asyncio.sleep(0.2)
        group_msg = [r for r in server.received if r.get("action") == "send_group_msg"][-1]
        segs = group_msg["params"]["message"]
        assert segs[0] == {"type": "at", "data": {"qq": "12345"}}, segs
        assert segs[1] == {"type": "reply", "data": {"id": "4"}}, segs
        assert segs[2]["type"] == "text", segs
        print("ok 群消息带 @ 与引用")

        # 8) 撤回
        assert await channel.recall("g:555"), "撤回失败"
        await asyncio.sleep(0.2)
        assert any(r.get("action") == "delete_msg" for r in server.received)
        print("ok 撤回")

        # 9) 戳一戳
        assert await channel.poke("g:555", "12345")
        poke = [r for r in server.received if r.get("action") == "send_poke"][-1]
        assert poke["params"] == {"user_id": 12345, "group_id": 555}, poke
        print("ok 戳一戳")

        # 10) 收到戳一戳：随机回一句，且不产生会话事件
        before = len([r for r in server.received if r.get("action") == "send_group_msg"])
        await server.send_event(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "time": 1762330000,
                "user_id": 12345,
                "target_id": 99999,
                "group_id": 555,
            }
        )
        replies: list[dict] = []
        for _ in range(50):
            await asyncio.sleep(0.1)
            replies = [r for r in server.received if r.get("action") == "send_group_msg"]
            if len(replies) > before:
                break
        assert len(replies) > before, "戳一戳没有回应"
        segs = replies[-1]["params"]["message"]
        assert segs[0] == {"type": "at", "data": {"qq": "12345"}}, segs
        assert segs[-1]["type"] == "text" and segs[-1]["data"]["text"], segs
        assert events.empty(), "戳一戳不该进会话"
        print("ok 戳一戳回话")

        # 10.5) 冷却期内再戳不回
        before_cd = len([r for r in server.received if r.get("action") == "send_group_msg"])
        await server.send_event(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "time": 1762330001,
                "user_id": 12345,
                "target_id": 99999,
                "group_id": 555,
            }
        )
        await asyncio.sleep(0.8)
        after_cd = len([r for r in server.received if r.get("action") == "send_group_msg"])
        assert after_cd == before_cd, "冷却期内不该再回戳"
        print("ok 戳一戳冷却")

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    shutil.rmtree(media, ignore_errors=True)

    print("\nQQ OneBot 渠道通过")


if __name__ == "__main__":
    asyncio.run(main())
