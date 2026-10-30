---
name: kubernetes-triage
description: Find unhealthy workloads in a Kubernetes namespace, explain each root cause from saved evidence, and write checked findings with proposed fixes.
---

# Kubernetes triage

Work from evidence. Every cluster read comes from a harness tool and is saved
under `evidence/`; read those files with the shell rather than relying on the
short preview a tool returns.

1. **Survey.** Call `list_resources` for `pods` and `deployments` in each
   namespace in scope, and `namespace_events` once per namespace.
2. **Pick suspects.** Run
   `python3 <skill-root>/scripts/triage.py evidence/*.txt`. It reads saved
   `kubectl get pods -o wide` output and prints each pod that is not ready,
   is waiting, or has restarted, with the evidence file it came from.
3. **Drill in.** For each suspect, `describe` the pod and read its logs with
   `pod_logs`. For a container that restarts, also read `previous=true`: the
   current container may not have logged anything yet. Use `describe` on the
   owning Deployment when replicas or rollout state matter.
4. **Diagnose.** Match the evidence to a cause:

   | Symptom in the evidence | Usual cause |
   | --- | --- |
   | `CrashLoopBackOff`, exit code 1, an error in the previous logs | The program fails at start: configuration, a dependency, or a bug |
   | `ErrImagePull` / `ImagePullBackOff`, "not found" or "manifest unknown" | A wrong image name or tag, or a missing registry credential |
   | `OOMKilled`, exit code 137 | The memory limit is too low for the workload |
   | `Pending` with `FailedScheduling` | Requests exceed free capacity, or selectors and taints do not match |
   | Running but not ready, `Unhealthy` probe events | The readiness probe fails: wrong port or path, or a slow start |

   State only what the evidence shows. If the cause is uncertain, say what
   would confirm it.
5. **Write the outputs.**
   - `output/findings.md`: one section per affected workload with the problem,
     the cause, the evidence (quoted) and the proposed fix. List healthy
     workloads briefly.
   - `output/findings.json`, written last:

     ```json
     {"findings": [{
       "resource": "deployment/checkout", "namespace": "sre-demo",
       "problem": "CrashLoopBackOff", "cause": "...", "fix": "...",
       "evidence": [{"file": "evidence/004-pod-logs.txt", "quote": "exact text from that file"}]
     }]}
     ```

     Each finding needs at least one evidence entry. A quote is text copied
     exactly from the named evidence file; whitespace differences are allowed.
6. Reopen both files and confirm every quote appears in its evidence file, for
   example with `grep -F`. Fix any mismatch before you answer.
