"""FishBot 评测运行器：python tests/eval/run.py [--only chat] [--json-out path]

HTTP 部分对运行中的服务打真实请求；media 部分直接调用本地模块。
默认会跑全部用例，结果同时写入 data/eval/report.json（已忽略，不进仓库）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import yaml  # noqa: E402

CASES = pathlib.Path(__file__).with_name("cases.yaml")
BAD_TEXT = ("Traceback", "AttributeError", "agent 出错", "Invalid params", "prompt is already in flight")


def _load_cases() -> dict[str, list[dict]]:
    data = yaml.safe_load(CASES.read_text(encoding="utf-8")) or {}
    return {str(key): list(value or []) for key, value in data.items()}


def _sse_turn(
    client: httpx.Client, base: str, session_id: str, user_id: str, text: str
) -> tuple[list[str], float | None, float]:
    """返回 (文本片段, 首个文本动作耗时, 本轮总耗时)。"""
    started = time.perf_counter()
    first_text: float | None = None
    pieces: list[str] = []
    payload = {"session_id": session_id, "user_id": user_id, "text": text}
    with client.stream("POST", f"{base}/api/chat", json=payload) as resp:
        if resp.status_code >= 400:
            detail = resp.read().decode("utf-8", "ignore")[:200]
            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") != "action":
                continue
            if event.get("kind") in ("text", "markdown", "approval", "image") and event.get("text"):
                now = time.perf_counter() - started
                if first_text is None:
                    first_text = now
                pieces.append(str(event["text"]))
    return pieces, first_text, time.perf_counter() - started


def _check(text: str, expect: dict) -> str:
    """通过返回空串，失败返回原因。长度按去掉空白后的字数算，避免分段换行干扰。"""
    compact = re.sub(r"\s+", "", text)
    for bad in BAD_TEXT:
        if bad in text:
            return f"出现异常文本：{bad}"
    for item in expect.get("must_contain") or []:
        if str(item) not in text:
            return f"缺少：{item}"
    for item in expect.get("must_not_contain") or []:
        if str(item) in text:
            return f"不应出现：{item}"
    if expect.get("regex") and not re.search(str(expect["regex"]), text):
        return f"正则未命中：{expect['regex']}"
    if expect.get("max_chars") and len(compact) > int(expect["max_chars"]):
        return f"太长：{len(compact)} > {expect['max_chars']}"
    return ""


def run_http_case(client: httpx.Client, base: str, case: dict, run_id: str) -> dict:
    category = case.get("_category", "")
    session_id = f"eval-{run_id}-{case['id']}"
    user_id = session_id
    turns = list(case.get("turns") or [case.get("input") or ""])
    try:
        client.post(
            f"{base}/api/reset",
            json={"session_id": session_id, "user_id": user_id},
            timeout=20,
        )
    except Exception:
        pass

    pieces: list[str] = []
    first_s: float | None = None
    total_s = 0.0
    for turn in turns:
        turn_pieces, turn_first, turn_total = _sse_turn(client, base, session_id, user_id, str(turn))
        pieces.extend(turn_pieces)
        if first_s is None and turn_first is not None:
            first_s = turn_first
        total_s += turn_total

    text = "\n".join(pieces).strip()
    reason = _check(text, case.get("expect") or {})
    expect = case.get("expect") or {}
    if not reason and expect.get("max_first_s") and first_s is not None:
        if first_s > float(expect["max_first_s"]):
            reason = f"首字太慢：{first_s:.1f}s > {expect['max_first_s']}s"
    if not reason and expect.get("max_total_s") and total_s > float(expect["max_total_s"]):
        reason = f"总耗时太慢：{total_s:.1f}s > {expect['max_total_s']}s"
    if first_s is None:
        first_s = total_s       # 没有文本动作时用总耗时兜底，避免统计缺口

    return {
        "id": case["id"],
        "category": category,
        "pass": not reason,
        "reason": reason,
        "chars": len(re.sub(r"\s+", "", text)),
        "first_s": round(first_s, 2),
        "total_s": round(total_s, 2),
        "text": text[:600],
    }


def _media_case(case: dict, captioner) -> dict:
    """媒体用例：直接调用模块，不经过 QQ，避免评测刷屏。"""
    from PIL import Image

    from app import docs, stickers, synth
    from app.files import animation_sheet

    started = time.perf_counter()
    kind = case.get("kind")
    text = ""
    reason = ""
    detail_path = ""
    frames = 0

    if kind in ("sticker", "image"):
        asset = stickers.available().get(str(case.get("asset") or ""))
        if asset is None:
            reason = f"找不到素材：{case.get('asset')}"
        else:
            text = asyncio.run(captioner(str(asset), kind))
            if not text:
                reason = "描述为空"
    elif kind == "gif":
        tmp = pathlib.Path(tempfile.mkdtemp()) / "eval.gif"
        frames_data = []
        for index in range(4):
            frame = Image.new("RGB", (160, 120), (30 + index * 50, 80, 160))
            frames_data.append(frame)
        frames_data[0].save(
            tmp, "GIF", save_all=True, append_images=frames_data[1:], duration=200, loop=0
        )
        info = animation_sheet(tmp)
        if info is None:
            reason = "没有抽出帧"
        else:
            frames = int(info["frames"])
            detail_path = str(info["path"])
            if frames < int((case.get("expect") or {}).get("min_frames", 2)):
                reason = f"帧数不足：{frames}"
    elif kind == "document":
        tmp = pathlib.Path(tempfile.mkdtemp()) / "eval.txt"
        tmp.write_text("EVAL-DOC-TOKEN\n第二行内容", encoding="utf-8")
        text = docs.extract(tmp, "eval.txt")
    elif kind == "synth":
        path = synth.render("评测通过", size=256, max_chars=24)
        if not path or not pathlib.Path(path).is_file():
            reason = "合成失败"
        else:
            detail_path = path
    else:
        reason = f"未知媒体类型：{kind}"

    expect = case.get("expect") or {}
    if not reason:
        reason = _check(text, expect)
    total_s = time.perf_counter() - started
    if not reason and expect.get("max_total_s") and total_s > float(expect["max_total_s"]):
        reason = f"耗时太长：{total_s:.1f}s > {expect['max_total_s']}s"
    if not reason and expect.get("max_chars") and not text:
        reason = "没有可检查的文本"

    return {
        "id": case["id"],
        "category": "media",
        "pass": not reason,
        "reason": reason,
        "chars": len(text),
        "first_s": round(total_s, 2),
        "total_s": round(total_s, 2),
        "frames": frames,
        "path": detail_path,
        "text": text[:300],
    }


def _stats(values: list[float]) -> dict:
    if not values:
        return {"p50": 0.0, "p95": 0.0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[p95_index], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--only", default="", help="分类或 id，逗号分隔")
    parser.add_argument("--json-out", default=str(ROOT / "data" / "eval" / "report.json"))
    parser.add_argument("--strict", action="store_true", help="有失败时返回非 0")
    args = parser.parse_args()

    only = {item.strip() for item in args.only.split(",") if item.strip()}
    cases = _load_cases()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[dict] = []

    def selected(category: str, case: dict) -> bool:
        if not only:
            return True
        return category in only or case.get("id") in only

    from app.config import load_config
    from app.credentials import inject_dsh_refs
    from app.vision import Captioner

    inject_dsh_refs()
    cfg = load_config()
    captioner = Captioner(cfg) if cfg.media.caption else None

    with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        for category in ("chat", "worker_chat", "task", "memory", "proactive", "meme"):
            for case in cases.get(category, []):
                if not selected(category, case):
                    continue
                item = dict(case)
                item["_category"] = category
                result = run_http_case(client, args.base_url, item, run_id)
                results.append(result)
                mark = "ok  " if result["pass"] else "FAIL"
                print(
                    f"{mark} {result['id']:<22} {result['total_s']:>6.1f}s "
                    f"{result['chars']:>4}字  {result['reason']}"
                )

        if captioner is not None:
            for case in cases.get("media", []):
                if not selected("media", case):
                    continue
                result = _media_case(case, captioner)
                results.append(result)
                mark = "ok  " if result["pass"] else "FAIL"
                print(f"{mark} {result['id']:<22} {result['total_s']:>6.1f}s  {result['reason']}")

    summary: dict[str, dict] = {}
    for category in sorted({item["category"] for item in results}):
        rows = [item for item in results if item["category"] == category]
        summary[category] = {
            "total": len(rows),
            "passed": sum(1 for item in rows if item["pass"]),
            "first_s": _stats([item["first_s"] for item in rows]),
            "total_s": _stats([item["total_s"] for item in rows]),
        }

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "base_url": args.base_url,
        "summary": summary,
        "results": results,
    }
    out = pathlib.Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    passed = sum(1 for item in results if item["pass"])
    print(f"\n总计：{passed}/{len(results)} 通过；报告：{out}")
    for category, stats in summary.items():
        print(
            f"  {category:<10} {stats['passed']}/{stats['total']}  "
            f"首响 p50 {stats['first_s']['p50']}s / p95 {stats['first_s']['p95']}s  "
            f"总耗时 p50 {stats['total_s']['p50']}s / p95 {stats['total_s']['p95']}s"
        )
    if args.strict and passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
