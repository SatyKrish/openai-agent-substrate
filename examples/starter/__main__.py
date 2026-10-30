# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Chat with the starter agent in the terminal: pass a question to ask it once, or none to chat."""

from __future__ import annotations

import argparse
import asyncio

from . import server as starter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    starter.add_arguments(parser)
    parser.add_argument("--thread", help="Continue this conversation (IDs are listed with /list)")
    parser.add_argument("question", nargs="*", help="Ask once and exit")
    args = parser.parse_args(argv)
    starter.resolve(parser, args)

    from agents import flush_traces

    from harness.terminal import TerminalChat

    server = starter.build(args)
    print(f"Starter agent · model {args.model} · template {args.template}", flush=True)
    chat = TerminalChat(server, title="Starter agent", jaeger_url=args.jaeger_url)
    chat.thread_id = args.thread
    try:
        asyncio.run(chat.run(" ".join(args.question) or None))
    except KeyboardInterrupt:
        pass
    finally:
        flush_traces()
        server.store.close()
    if chat.thread_id:
        print(f"Conversation {chat.thread_id}: continue with --thread {chat.thread_id}, or open it in starter-ui.")


if __name__ == "__main__":
    main()
