"""task_step 结构化任务状态测试：python tests/task_step_test.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.store import Store  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def main() -> None:
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    store.init()
    task_id = store.task_start("web", "u:1", "main")
    steps = [
        {"goal": "读取文件", "check": "输出第一行"},
        {"goal": "统计数量", "check": "输出数字"},
        {"goal": "汇总", "check": "两行结果"},
    ]
    store.task_step_reset(task_id, steps)
    rows = store.task_steps(task_id)
    check("计划写入三行", len(rows) == 3, str(len(rows)))
    check("初始状态 pending", all(row["status"] == "pending" for row in rows))

    store.task_step_start(task_id, 1)
    row = store.task_steps(task_id)[0]
    check("开始后 running", row["status"] == "running" and row["attempts"] == 1)

    store.task_step_finish(task_id, 1, True, "# FishBot")
    row = store.task_steps(task_id)[0]
    check("通过后 passed", row["status"] == "passed" and "# FishBot" in row["evidence"])

    store.task_step_start(task_id, 2)
    store.task_step_start(task_id, 2)
    store.task_step_finish(task_id, 2, False, "没有输出数字", "校验未通过")
    row = store.task_steps(task_id)[1]
    check("重试次数累加", row["attempts"] == 2, str(row["attempts"]))
    check("失败原因留档", row["status"] == "failed" and "校验未通过" in row["evidence"])

    store.task_step_interrupt(task_id, "进程重启")
    row = store.task_steps(task_id)[2]
    check("中断步骤标失败", row["status"] == "failed" and "进程重启" in row["evidence"])

    store.task_finish(task_id, "unverified", "部分步骤未通过")
    check("任务状态同步", store.task_steps(task_id)[0]["status"] == "passed")

    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
