"""对运行中的服务打一轮真实请求：python tests/e2e_http.py [端口]"""
from __future__ import annotations

import json
import sys

import httpx

PORT = sys.argv[1] if len(sys.argv) > 1 else "8010"
BASE = f"http://127.0.0.1:{PORT}"


def main() -> None:
    with httpx.Client(timeout=60) as client:
        print("healthz:", client.get(f"{BASE}/healthz").json())

        payload = {"session_id": "e2e", "user_id": "local", "text": "在吗"}
        with client.stream("POST", f"{BASE}/api/chat", json=payload) as resp:
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                evt = json.loads(line[5:].strip())
                if evt["type"] == "action":
                    print(f"  action kind={evt['kind']} delay={evt['delay']} text={evt['text']!r}")

        print("history:", client.get(f"{BASE}/api/history", params={"session_id": "e2e"}).json())
        print("reset:", client.post(f"{BASE}/api/reset", json={"session_id": "e2e"}).json())


if __name__ == "__main__":
    main()
