# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Serve the starter agent's UI on loopback; it shows the same conversations as the terminal chat."""

from __future__ import annotations

import argparse

from .. import server as starter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    starter.add_arguments(parser)
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args(argv)
    starter.resolve(parser, args)

    import logging

    import uvicorn
    from agents import flush_traces

    from openai_harness.capacity import WorkerCapacity

    from .app import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    server = starter.build(args, base_url=f"http://localhost:{args.port}")
    config = {"model": args.model, "template": args.template, "context": args.context, "atespace": args.atespace, "prompts": [
        {"label": "Primes with Python", "prompt": "Find the primes below 200 with Python and save them as output/primes.csv."},
        {"label": "Inspect the sandbox", "prompt": "What OS, Python version and tools does your sandbox have? "
                                                   "Save a short report as output/sandbox.md."},
    ]}
    capacity = WorkerCapacity(server.client.control, atespace=args.atespace, template=args.template)
    app = create_app(server, config=config, port=args.port, jaeger_url=args.jaeger_url, capacity=capacity)
    print(f"Starter agent UI: http://localhost:{args.port}  (model {args.model}, template {args.template})", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    finally:
        flush_traces()
        server.store.close()


if __name__ == "__main__":
    main()
