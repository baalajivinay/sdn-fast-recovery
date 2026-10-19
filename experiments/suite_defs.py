"""
Experiment matrix shared by the real suite (run_suite.py) and the simulator
(sim/simulate.py), so both always run exactly the same configurations and
the same failures.
"""

import random

from sdnrec.routing import path

# (scheme, bfd_ms or None, failure)
EXPERIMENTS = {
    "e1": dict(topology="triangle", trials=10, link_delay_ms=0, control_delays=[10],
               configs=[("restoration", None, "carrier"),
                        ("protection", None, "carrier"),
                        ("restoration", None, "silent"),
                        ("protection", None, "silent"),
                        ("restoration", 5, "silent"),
                        ("protection", 1, "silent"),
                        ("protection", 5, "silent"),
                        ("protection", 15, "silent")]),
    "e2": dict(topology="ring5", trials=10, link_delay_ms=1, control_delays=[10],
               configs=[("protection", 1, "silent"),
                        ("protection", 5, "silent"),
                        ("protection", 15, "silent"),
                        ("restoration", 5, "silent"),
                        ("hybrid", 5, "silent")]),
    "e3": dict(topology="usnet", trials=30, link_delay_ms=0, control_delays=[10],
               configs=[("protection", 5, "silent"),
                        ("protection", 15, "silent"),
                        ("restoration", 5, "silent"),
                        ("hybrid", 5, "silent")]),
    "e4": dict(topology="ring5", trials=5, link_delay_ms=1, control_delays=[0, 5, 10, 25, 50],
               configs=[("restoration", 5, "silent"),
                        ("protection", 5, "silent"),
                        ("hybrid", 5, "silent")]),
}

QUICK = {"e1": 3, "e2": 3, "e3": 8, "e4": 2}


def config_id(scheme, bfd, failure, cdelay):
    det = "bfd%d" % bfd if bfd else "los"
    return "%s_%s_%s_cd%d" % (scheme, det, failure, cdelay)


def pick_failure(topo, trial, seed):
    """Same trial index -> same flow and same cut, in every configuration."""
    if topo.fixed_failure:
        src, dst = topo.flows[0]
        return src, dst, topo.fixed_failure
    rng = random.Random(seed * 1000 + trial)
    src, dst = rng.choice(topo.flows)
    p = path(topo, topo.hosts[src], topo.hosts[dst])
    i = rng.randrange(len(p) - 1)
    return src, dst, (p[i], p[i + 1])
