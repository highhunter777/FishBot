"""图片与收发目录测试：python tests/media_test.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.openai_adapter import OpenAiAdapter  # noqa: E402
from app.files import data_url, day_dir  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def test_day_dir() -> None:
    print("日期目录")
    base = pathlib.Path(tempfile.mkdtemp())
    folder = day_dir(base, "inbox")
    check("路径含日期", folder.name == date.today().isoformat(), folder.name)
    check("按类型分", folder.parent.name == "inbox", str(folder))
    check("目录已创建", folder.is_dir())
    out = day_dir(base, "outbox")
    check("outbox 独立", out != folder and out.parent.name == "outbox")


def test_data_url() -> None:
    print("data URL")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "a.png"
    tmp.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    url = data_url(tmp)
    check("生成 data url", url.startswith("data:image/png;base64,"), str(url)[:30])
    check("文件不存在返回 None", data_url(tmp.with_name("nope.png")) is None)


def test_attach_images() -> None:
    print("图片塞进消息")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "b.jpg"
    tmp.write_bytes(b"fakejpg")

    messages = [{"role": "user", "content": "这是什么"}]
    out = OpenAiAdapter._attach_images(messages, [str(tmp)])
    content = out[-1]["content"]
    check("content 变成数组", isinstance(content, list), type(content).__name__)
    check("保留原文", content[0] == {"type": "text", "text": "这是什么"})
    check("带 image_url", content[1]["type"] == "image_url", str(content[1])[:40])
    check("base64 内联", content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    no_img = OpenAiAdapter._attach_images([{"role": "user", "content": "hi"}], [])
    check("没有图就原样返回", no_img[-1]["content"] == "hi")

    check(
        "挑出图片路径",
        OpenAiAdapter._image_paths(
            [{"kind": "image", "path": "/x/a.jpg"}, {"kind": "file", "path": "/x/b.zip"}, {"kind": "image"}]
        )
        == ["/x/a.jpg"],
    )


def main() -> None:
    test_day_dir()
    test_data_url()
    test_attach_images()
    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
