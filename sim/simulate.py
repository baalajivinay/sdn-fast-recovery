#!/usr/bin/env python3
"""
Model-based stand-in for the Mininet suite -- produces DRAFT results.

    python3 sim/simulate.py [--out results_sim]  &&  python3 experiments/analyze.py --results-dir results_sim

It runs the same experiment matrix as experiments/run_suite.py and writes
the same files (meta.json, arrivals.csv, controller_events.jsonl,
ovs_state.json), so the very same analyze.py and figures consume it. Nothing
here is a measurement; every output is stamped "simulated".

What is exact and what is modelled
----------------------------------
exact     which flow and which link fail (same seeds as the suite), the
          primary path, the fast-failover detour (pushed through the real
          protection tables, crankback included), the post-restoration path,
          and the forwarding-state size of every scheme.
modelled  timing. Parameters below, with where each number comes from:

  FF_SWITCH      OVS re-validation after a port goes dead. Calibrated so that
                 carrier-loss protection lands at ~2.7 ms, matching the
                 previous project's Mininet measurement (3.00 +- 0.47 ms).
  CARRIER_DET    veth carrier change seen by OVS (netlink), sub-millisecond.
  BFD detection  M*T minus the phase of the last hello: uniform in
                 [(M-1)T, MT]. Standard BFD timing (RFC 5880); the paper
                 bounds it the same way.
  RESTORE_FIXED  controller-side processing + flow installation. The previous
                 project measured restoration = 5.6 ms + 2 x one-way delay
                 with carrier detection; 5.6 - CARRIER_DET - small = 4.8 ms.
  COMPUTE        route recomputation for every destination (24 on USNET).
"""

import argparse
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments"))

from sdnrec import topologies                                       # noqa: E402
from sdnrec.routing import (state_for, forward, path, Blackhole)   # noqa: E402

from suite_defs import EXPERIMENTS, config_id, pick_failure      # noqa: E402

MULT = 3
FF_SWITCH = (1.9, 0.4)
CARRIER_DET = (0.8, 0.2)
RESTORE_FIXED = (4.8, 1.0)
COMPUTE = {"triangle": 0.2, "ring5": 0.3, "usnet": 3.0}
REOPT_EXTRA = (1.5, 0.5)
BASE_LAT, PER_HOP, JITTER = 0.06, 0.02, 0.015     # ms


def g(rng, mu_sd, lo=0.05):
    return max(lo, rng.gauss(*mu_sd))


def path_latency(topo, p, link_delay):
    hops = len(p) - 1
    w = sum(topo.weight(a, b) for a, b in zip(p, p[1:]))
    return BASE_LAT + hops * PER_HOP + w * link_delay


def simulate_trial(topo, scheme, bfd, failure, cdelay, trial, cfg_dir, spec, args, rng):
    tdir = os.path.join(cfg_dir, "trial%02d" % trial)
    os.makedirs(tdir, exist_ok=True)
    src, dst, (up, down) = pick_failure(topo, trial, args.seed)
    ld = spec["link_delay_ms"]
    prim = path(topo, topo.hosts[src], topo.hosts[dst])
    opt = path(topo, topo.hosts[src], topo.hosts[dst], {(up, down)})
    flows, groups = state_for("protection", topo)
    try:
        detour = forward(topo, flows, groups, src, dst, failed={(up, down)})
    except Blackhole:
        detour = None

    # ---- timing model (ms after the cut) --------------------------------
    if failure == "carrier" and not bfd:
        detect = g(rng, CARRIER_DET)
    elif bfd:
        detect = MULT * bfd - rng.uniform(0, bfd) + abs(rng.gauss(0.2, 0.1))
    else:
        detect = math.inf                    # silent failure, LoS only: nobody notices
    ctrl_rx = detect + cdelay                # PORT_STATUS reaches the controller
    restore_at = detect + 2 * cdelay + g(rng, RESTORE_FIXED) + COMPUTE[topo.name]
    if scheme == "protection":
        recover_at, new_path = detect + g(rng, FF_SWITCH), detour
        reopt_at = math.inf
    elif scheme == "hybrid":
        recover_at, new_path = detect + g(rng, FF_SWITCH), detour
        reopt_at = restore_at + g(rng, REOPT_EXTRA)
    else:
        recover_at, new_path = restore_at, opt
        reopt_at = math.inf

    pps, dur, cut = args.pps, args.duration, args.cut_at
    iv = 1.0 / pps
    origin = 1.8e9 + trial * 100.0 + rng.random()
    tf = origin + cut + rng.uniform(0, iv)
    lat_p = path_latency(topo, prim, ld)
    lat_new = path_latency(topo, new_path, ld) if new_path else None
    lat_opt = path_latency(topo, opt, ld)
    rows = []
    for seq in range(int(dur * pps)):
        s = origin + seq * iv + abs(rng.gauss(0, 0.00001))
        rel = (s - tf) * 1e3
        if rel < 0:
            lat = lat_p
        elif rel < recover_at:
            continue
        elif rel < reopt_at:
            lat = lat_new
        else:
            lat = lat_opt
        lat += rng.expovariate(1 / JITTER)
        rows.append((seq, s, s + lat / 1e3))
    with open(os.path.join(tdir, "arrivals.csv"), "w") as fh:
        fh.write("seq,send_time,recv_time\n")
        for seq, s, r in rows:
            fh.write("%d,%.6f,%.6f\n" % (seq, s, r))

    ev = [{"t": origin - 3, "ev": "start", "scheme": scheme}, {"t": origin - 2, "ev": "ready"}]
    if math.isfinite(ctrl_rx):
        ev.append({"t": tf + ctrl_rx / 1e3, "ev": "failure_detected"})
        if scheme == "restoration":
            ev.append({"t": tf + (restore_at + cdelay) / 1e3, "ev": "restoration_complete"})
        if scheme == "hybrid":
            ev.append({"t": tf + (reopt_at + cdelay) / 1e3, "ev": "reopt_complete"})
    with open(os.path.join(tdir, "controller_events.jsonl"), "w") as fh:
        for e in ev:
            fh.write(json.dumps(e) + "\n")

    meta = dict(topology=topo.name, scheme=scheme, bfd_ms=bfd, failure=failure,
                control_delay_ms=cdelay, trial=trial, src=src, dst=dst, cut=[up, down],
                cut_labels=[topo.label(up), topo.label(down)], t_fail=tf, t_fail_after=tf,
                pps=pps, duration=dur, cut_at=cut, link_delay_ms=ld, primary=prim,
                reroute_optimal=opt, detour=detour, simulated=True)
    with open(os.path.join(tdir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=1)

    if trial == 1:
        f, gr = state_for(scheme, topo)
        per = {s: {"flows": sum(1 for x in f if x.sw == s),
                   "groups": sum(1 for x in gr if x.sw == s)} for s in topo.switches}
        with open(os.path.join(cfg_dir, "ovs_state.json"), "w") as fh:
            json.dump(per, fh, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "results_sim"))
    ap.add_argument("--pps", type=float, default=5000)
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--cut-at", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=2014)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "SIMULATED"), "w") as fh:
        fh.write("Synthetic traces from sim/simulate.py. Not measurements.\n")
    rng = random.Random(args.seed)
    for name, spec in EXPERIMENTS.items():
        topo = topologies.get(spec["topology"])
        out = os.path.join(args.out, name)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "experiment.json"), "w") as fh:
            json.dump(dict(spec, name=name, pps=args.pps, simulated=True,
                           description=topo.description), fh, indent=1)
        for cdelay in spec["control_delays"]:
            for scheme, bfd, failure in spec["configs"]:
                cfg_dir = os.path.join(out, config_id(scheme, bfd, failure, cdelay))
                os.makedirs(cfg_dir, exist_ok=True)
                for trial in range(1, spec["trials"] + 1):
                    simulate_trial(topo, scheme, bfd, failure, cdelay, trial, cfg_dir,
                                   spec, args, rng)
        print("simulated", name)


if __name__ == "__main__":
    main()
