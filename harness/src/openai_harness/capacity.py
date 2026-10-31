# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Free sandbox slots for one ActorTemplate, as a UI shows them before a turn has to wait.

Substrate places an Actor on a worker with room for the template's CPU and memory.
``kubectl ate get worker`` reports each worker's capacity (JSON) and use (table);
the template's resource limits and worker selector say which workers count and how
many more Actors fit. This is an estimate for people, not a reservation.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

_UNITS = {"": 1, "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3}
_QUANTITY = re.compile(r"(\d+)(Ki|Mi|Gi|)")


def _bytes(text: str) -> int:
    match = _QUANTITY.fullmatch(text)
    if match is None:
        raise ValueError(f"unexpected quantity {text!r}")
    return int(match[1]) * _UNITS[match[2]]


def usage(table: str) -> dict[str, tuple[int, int]]:
    """Worker name -> (used millicores, used bytes), from ``kubectl ate get worker``'s table."""
    lines = [line.split() for line in table.splitlines() if line.strip()]
    if not lines:
        return {}
    header = lines[0]
    name, cpu, memory = header.index("NAME"), header.index("CPU(MILLICORES)"), header.index("MEMORY")
    return {row[name]: (int(row[cpu].split("/")[0]), _bytes(row[memory].split("/")[0]))
            for row in lines[1:] if len(row) == len(header)}


def free_slots(workers: list[dict], used: dict[str, tuple[int, int]], template: dict) -> dict:
    """Slots on the active workers the template selects, in total and free now."""
    limits = template.get("resources", {}).get("limits", {})
    cpu, memory = int(limits["cpuMillicores"]), int(limits["memoryBytes"])
    selector = (template.get("workerSelector") or {}).get("matchLabels") or {}
    slots = free = count = 0
    for worker in workers:
        status = worker.get("status", {})
        labels = worker.get("labels") or {}
        if status.get("state") != "WORKER_STATE_ACTIVE" or any(labels.get(key) != value for key, value in selector.items()):
            continue
        capacity = status.get("capacity", {})
        total_cpu, total_memory = int(capacity.get("cpuMillicores", 0)), int(capacity.get("memoryBytes", 0))
        used_cpu, used_memory = used.get(worker["metadata"]["name"], (0, 0))
        count += 1
        slots += min(total_cpu // cpu, total_memory // memory)
        free += max(min((total_cpu - used_cpu) // cpu, (total_memory - used_memory) // memory), 0)
    return {"workers": count, "slots": slots, "free": free}


class WorkerCapacity:
    """Calls ``kubectl ate`` with the same context as the agent's ``KubectlActorControl``; results are cached briefly."""

    def __init__(self, control, *, atespace: str, template: str, ttl: float = 5.0, timeout: float = 20.0):
        self.base = ["kubectl", "ate", "--context", control.context]
        if getattr(control, "kubeconfig", None):
            self.base += ["--kubeconfig", str(control.kubeconfig)]
        if getattr(control, "endpoint", None):
            self.base += ["--endpoint", control.endpoint]
        self.atespace = atespace
        self.template_name = template
        self.ttl = ttl
        self.timeout = timeout
        self._template: dict | None = None
        self._cached: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    async def _kubectl(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(*self.base, *args, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.DEVNULL)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            raise
        if process.returncode:
            raise RuntimeError(f"kubectl ate {' '.join(args[:2])} failed (exit {process.returncode})")
        return stdout.decode()

    async def __call__(self) -> dict:
        async with self._lock:
            if self._cached and time.monotonic() - self._cached[0] < self.ttl:
                return self._cached[1]
            if self._template is None:
                self._template = json.loads(await self._kubectl(
                    "get", "actor-template", self.template_name, "--atespace", self.atespace, "-o", "json"))
            workers = json.loads(await self._kubectl("get", "worker", "-o", "json")).get("workers", [])
            result = free_slots(workers, usage(await self._kubectl("get", "worker")), self._template)
            self._cached = (time.monotonic(), result)
            return result
