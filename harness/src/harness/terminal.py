# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""A terminal chat with any agent on the harness.

It drives the same HarnessChatServer in-process, so a terminal conversation is
an ordinary ChatKit thread in the agent's store: its UI lists it, and either
front end can continue it (one at a time). Text from the model and the sandbox
is untrusted, so control characters are removed before it reaches the terminal.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Awaitable, Callable

from chatkit.store import NotFoundError

from .chat import HarnessChatServer

HELP = ("Type a question. /new starts a conversation, /list shows recent ones, /open ID continues one, "
        "/files lists this conversation's files, /quit exits.")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BADGE = {"success": "✓", "danger": "✗", "warning": "!"}
_TOOL = re.compile(r"[A-Za-z0-9_.-]+")
_PATCH_MARK = re.compile(r"\*\*\* (Begin|End) Patch")


def clean(text: str) -> str:
    """Untrusted text without terminal control sequences; newlines and tabs stay."""
    return _CONTROL.sub("", text)


def message(text: str) -> dict:
    return {"content": [{"type": "input_text", "text": text}], "attachments": [], "inference_options": {}}


async def _default_read() -> str | None:
    try:
        return await asyncio.to_thread(input, "you> ")
    except EOFError:
        return None


def _default_write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


class TerminalChat:
    def __init__(self, server: HarnessChatServer, *, title: str, jaeger_url: str | None = None,
                 read: Callable[[], Awaitable[str | None]] = _default_read, write: Callable[[str], None] = _default_write):
        self.server = server
        self.title = title
        self.jaeger_url = jaeger_url
        self.read = read
        self.write = write
        self.thread_id: str | None = None

    def line(self, text: str = "") -> None:
        self.write(text + "\n")

    async def ask(self, text: str) -> None:
        """Send one message, creating the conversation on the first one, and show the turn as it streams."""
        if self.thread_id is None:
            payload = {"type": "threads.create", "params": {"input": message(text)}}
        else:
            payload = {"type": "threads.add_user_message", "params": {"thread_id": self.thread_id, "input": message(text)}}
        result = await self.server.process(json.dumps(payload), {})
        shown: set[str] = set()
        said: dict[str, str] = {}  # assistant message ID -> the text already written
        speaking = False

        def say(item_id: str, text: str) -> None:
            # Live models stream deltas; a message may also arrive whole. Write only what is new.
            nonlocal speaking
            before = said.get(item_id, "")
            if not text.startswith(before):
                return
            if text[len(before):]:
                if not speaking:
                    self.write("agent> " + before)
                    speaking = True
                self.write(text[len(before):])
            said[item_id] = text

        async for chunk in result:
            for raw in chunk.decode().splitlines():
                if not raw.startswith("data: "):
                    continue
                event = json.loads(raw[6:])
                kind = event.get("type")
                item = event.get("item") or {}
                update = event.get("update") or {}
                if kind == "thread.created":
                    self.thread_id = event["thread"]["id"]
                    self.line(f"  conversation {self.thread_id}")
                elif kind == "error":
                    if speaking:
                        self.line()
                        speaking = False
                    self.line(f"  ✗ {clean(event.get('message') or 'The turn failed.')}")
                elif item.get("type") == "workflow":
                    for index, task in enumerate(item["workflow"]["tasks"]):
                        speaking = self._task(f"{item['id']}:{index}", task, shown, speaking)
                elif update.get("type") in ("workflow.task.added", "workflow.task.updated"):
                    speaking = self._task(f"{event['item_id']}:{update['task_index']}", update["task"], shown, speaking)
                elif update.get("type") == "assistant_message.content_part.text_delta":
                    say(event["item_id"], said.get(event["item_id"], "") + clean(update["delta"]))
                elif item.get("type") == "assistant_message":
                    say(item["id"], clean("".join(part.get("text", "") for part in item.get("content", []))))
                    if kind == "thread.item.done" and speaking:
                        self.line()
                        speaking = False
                elif kind == "thread.item.done" and item.get("type") == "widget":
                    if speaking:
                        self.line()
                        speaking = False
                    self._card(item["widget"])
        if speaking:
            self.line()
        await self._after_turn()

    def _task(self, key: str, task: dict, shown: set[str], speaking: bool) -> bool:
        # Workspace steps and tool calls; the model's reasoning summaries are not shown.
        if task.get("type") != "custom" or not task.get("title"):
            return speaking
        title = clean(task["title"])
        done = task.get("status_indicator") == "complete"
        tool = _TOOL.fullmatch(title.split(" · ")[0]) is not None
        if tool and not done:
            return speaking  # a tool call is shown once, when it finishes
        # A harness step is shown when it starts and when it ends; its progress (the wait time) is not repeated.
        mark = f"{key}:{'done' if done else title.split(' · ')[0]}"
        if mark in shown:
            return speaking
        shown.add(mark)
        if speaking:
            self.line()
        detail = ""
        if tool:
            # The first line of the call's arguments, such as the command it ran.
            # A patch shows its first file operation, not its "*** Begin Patch" header.
            arguments = [row for row in clean(task.get("content") or "").splitlines()
                         if row and not row.startswith("`") and not _PATCH_MARK.fullmatch(row.strip())]
            detail = f"  {arguments[0][:100]}" if arguments else ""
        self.line(f"  {'·' if done else '…'} {title}{detail}")
        return False

    def _card(self, widget: dict) -> None:
        files = []

        def walk(node):
            kind = node.get("type")
            if kind == "Badge":
                self.line(f"  {_BADGE.get(node.get('color'), '·')} {clean(node.get('label', ''))}")
            elif kind == "Text" and node.get("value"):
                self.line(f"    {clean(node['value'])}")
            elif kind == "Button" and (node.get("onClickAction") or {}).get("type") == "open_file":
                files.append(node["onClickAction"]["payload"]["path"])
            elif kind == "Caption" and node.get("value"):
                self.line(f"  {clean(node['value'])}")
            for child in node.get("children") or []:
                walk(child)

        walk(widget)

    async def _after_turn(self) -> None:
        """Where this turn's new files are on the host, and its trace."""
        if self.thread_id is None:
            return
        thread = await self.server.store.load_thread(self.thread_id, {})
        turns = thread.metadata.get("turns", [])
        if not turns:
            return
        turn = turns[-1]
        base = self.server.conversation(thread).turn_dir(turn["number"])
        for item in turn["files"]:
            if item.get("changed") and item["path"].startswith("output/") and not item.get("too_large"):
                self.line(f"  saved {item['path']}  →  {base / item['path']}")
        if turn.get("otel_trace_id") and self.jaeger_url:
            self.line(f"  trace {self.jaeger_url.rstrip('/')}/trace/{turn['otel_trace_id']}")

    async def files(self) -> None:
        if self.thread_id is None:
            self.line("  No conversation yet.")
            return
        thread = await self.server.store.load_thread(self.thread_id, {})
        latest = {}
        for turn in thread.metadata.get("turns", []):
            for item in turn["files"]:
                if not item.get("too_large"):
                    latest[item["path"]] = turn["number"]
        conversation = self.server.conversation(thread)
        for path, number in sorted(latest.items()):
            self.line(f"  {path}  →  {conversation.turn_dir(number) / path}")
        if not latest:
            self.line("  No files yet.")

    async def open(self, thread_id: str) -> None:
        try:
            thread = await self.server.store.load_thread(thread_id, {})
        except NotFoundError:
            self.line(f"  No conversation {clean(thread_id)}; /list shows recent ones.")
            return
        self.thread_id = thread.id
        self.line(f"  continuing {thread.id}: {clean(thread.title or '(untitled)')}")

    async def recent(self) -> None:
        page = await self.server.store.load_threads(10, None, "desc", {})
        for thread in page.data:
            turns = len(thread.metadata.get("turns", []))
            self.line(f"  {thread.id}  {clean(thread.title or '(untitled)')}  · {turns} turn{'s' if turns != 1 else ''}")
        if not page.data:
            self.line("  No conversations yet.")

    async def run(self, question: str | None = None) -> str | None:
        """One question and exit, or an interactive session. Returns the conversation's ID."""
        if question:
            await self.ask(question)
            return self.thread_id
        self.line(f"{self.title}. {HELP}")
        if self.thread_id:
            self.line(f"  continuing {self.thread_id}")
        while (text := await self.read()) is not None:
            text = text.strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/help":
                self.line(HELP)
            elif text == "/new":
                self.thread_id = None
                self.line("  New conversation: your next message starts it.")
            elif text == "/list":
                await self.recent()
            elif text.startswith("/open "):
                await self.open(text.split(maxsplit=1)[1])
            elif text == "/files":
                await self.files()
            elif text.startswith("/"):
                self.line(f"  Unknown command. {HELP}")
            else:
                await self.ask(text)
        return self.thread_id
