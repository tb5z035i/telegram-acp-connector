from __future__ import annotations

import json
import os
import sys


def send(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    session_counter = 0

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"loadSession": False},
                        "agentInfo": {
                            "name": os.environ.get("FAKE_ACP_NAME", "fake-acp"),
                            "title": "Fake ACP",
                            "version": "1.0.0",
                        },
                        "authMethods": [],
                    },
                }
            )
            continue

        if method == "session/new":
            session_counter += 1
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": f"session-{session_counter}"},
                }
            )
            continue

        if method == "session/prompt":
            params = message["params"]
            session_id = params["sessionId"]
            prompt_text = params["prompt"][0]["text"]
            rendered = f"Echo: {prompt_text}"
            midpoint = max(1, len(rendered) // 2)
            for chunk in (rendered[:midpoint], rendered[midpoint:]):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "text": chunk,
                            },
                        },
                    }
                )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"stopReason": "end_turn"},
                }
            )
            continue

        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"message": f"Unknown method: {method}"},
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
