"""Minimal local App Server stand-in for transport tests.

Never runs commands, touches project files, or calls OpenAI.
"""
import json
import sys

pending = None


def emit(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method = msg.get("method")
    ident = msg.get("id")
    if method == "initialize":
        emit({"id": ident, "result": {"userAgent": "test-only"}})
    elif method == "thread/start":
        emit({"id": ident, "result": {"thread": {"id": "mock-thread"}}})
    elif method == "turn/start":
        emit({"method": "turn/started", "params": {
            "threadId": "mock-thread", "turn": {"id": "mock-turn"}}})
        emit({"id": ident, "result": {"turn": {"id": "mock-turn"}}})
        emit({"method": "item/agentMessage/delta", "params": {
            "threadId": "mock-thread", "turnId": "mock-turn",
            "delta": "Message from fake subprocess. "}})
        emit({"method": "item/started", "params": {
            "threadId": "mock-thread", "turnId": "mock-turn",
            "item": {"id": "item-1", "type": "commandExecution",
                     "command": "python -m unittest",
                     "cwd": "C:\\project", "status": "inProgress"}}})
        emit({"id": 987, "method": "item/commandExecution/requestApproval",
              "params": {"threadId": "mock-thread", "turnId": "mock-turn",
                         "itemId": "item-1", "reason": "Tests",
                         "availableDecisions": ["accept", "decline"]}})
        pending = 987
    elif ident == pending and "result" in msg:
        pending = None
        decision = msg["result"].get("decision")
        emit({"method": "serverRequest/resolved", "params": {
            "threadId": "mock-thread", "requestId": 987}})
        emit({"method": "item/agentMessage/delta", "params": {
            "threadId": "mock-thread", "turnId": "mock-turn",
            "delta": f"Decision: {decision}."}})
        emit({"method": "turn/completed", "params": {
            "threadId": "mock-thread",
            "turn": {"id": "mock-turn", "status": "completed"}}})
    elif method == "turn/interrupt":
        emit({"id": ident, "result": {}})
        emit({"method": "turn/completed", "params": {
            "threadId": "mock-thread",
            "turn": {"id": "mock-turn", "status": "interrupted"}}})
