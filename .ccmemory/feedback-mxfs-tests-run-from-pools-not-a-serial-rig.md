---
name: feedback-mxfs-tests-run-from-pools-not-a-serial-rig
description: Never call mxfs's rig serial: it allocates VMs and LUNs from pools by script, so laps run in parallel; read the allocator before asserting limits.
metadata:
  type: feedback
---

mxfs tests run on pools: `tools/vm_pool.sh` + `tests/lib/vmpool.sh` (`vmpool_take <what> <count> --role ...`) hand out VMs, `tools/lun_pool.sh` hands out LUNs, each configuration has its own run lock. Laps on separate lanes run side by side; only laps re-prepping the same fleet serialize.

**Why:** a ccenv session quoted one line of `tests/lap_queue.sh` ("the rig is serial") and told the user ccdirector could not run 4x because testing was serial. The user: "MXFS uses a fucking goddamn pool ... a POOL OF FUCKING VMS AND A FUCKING POOL OF IMAGES AND IT HAS FUCKING SCRIPTS TO ALLOC FROM THAT POOL". The release-blocking defects (21 stability/integrity) span ~10 configurations and mostly reproduce on 1-2 nodes of a 32-node pool — parallel by design.

**How to apply:** before claiming a project's work or tests cannot parallelize, read how it allocates the resource (its pool/allocator scripts), not a comment in one harness. When parallel workers underperform, look first at the orchestrator's own overhead (session churn, parks, idle workers), not at the project.
