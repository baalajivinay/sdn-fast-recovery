#!/usr/bin/env python3
"""
Experiment suite for the van Adrichem et al. (EWSDN 2014) replication.

    python3 experiments/run_suite.py --exp e1 [--trials N] [--quick]
    python3 experiments/run_suite.py --exp all --quick

Each configuration is (scheme, BFD interval, failure type, controller delay).
Within an experiment the network is built once; every trial restarts the
controller so it begins from clean tables, then cuts one link mid-stream.

  e1  triangle (paper Fig. 3a)   detection: LoS vs BFD 1/5/15 ms, carrier vs
                                 silent failure, restoration vs protection
  e2  ring5    (paper Fig. 3b)   interior cut C-D -> crankback; BFD 1/5/15 ms;
                                 restoration and hybrid for comparison.
                                 1 ms per unit of weight (E-A = 10 ms) so the
                                 detour shows up in latency.
  e3  usnet    (paper Fig. 3c)   24 nodes / 43 links, random cut on one of 20
                                 longest primaries; BFD 5/15 ms (the paper
                                 used 5 ms here: 1 ms overloads software OVS)
  e4  ring5                      controller distance sweep 0..50 ms one-way

Output: results/<exp>/<config>/trialNN/{arrivals.csv, meta.json, ...}
"""

import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments"))

from mininet.log import setLogLevel                  # noqa: E402

from sdnrec import topologies                        # noqa: E402
from sdnrec.routing import path                      # noqa: E402
import testbed as tb                                 # noqa: E402

TRAFFIC = os.path.join(ROOT, "experiments", "traffic.py")

from suite_defs import EXPERIMENTS, QUICK, config_id, pick_failure  # noqa: E402


def wait_file(f, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(f):
            return True
        time.sleep(0.05)
    return False


def run_trial(net, topo, scheme, bfd, failure, cdelay, trial, cfg_dir, args):
    tdir = os.path.join(cfg_dir, "trial%02d" % trial)
    os.makedirs(tdir, exist_ok=True)
    src, dst, (up, down) = pick_failure(topo, trial, args.seed)

    tb.clear_control_delay()
    if failure == "silent":
        tb.prepare_silent(topo, (up, down))
    ctrl = tb.Controller(topo, scheme, tdir)
    try:
        ctrl.wait_ready()
        if bfd:
            tb.wait_bfd_up(topo)
        state = None
        if trial == 1:
            state = tb.dump_state(topo)
        tb.set_control_delay(cdelay)
        time.sleep(args.settle)

        hs, hd = net.get(src), net.get(dst)
        ping = hs.cmd("ping -c 1 -W 2 %s" % topo.host_ip(dst))
        if "1 received" not in ping:
            raise RuntimeError("no connectivity %s->%s before the cut:\n%s" % (src, dst, ping))

        rx_ready, tx_ready = os.path.join(tdir, ".rx"), os.path.join(tdir, ".tx")
        for f in (rx_ready, tx_ready):
            if os.path.exists(f):
                os.remove(f)
        hd.cmd("python3 %s recv --out %s/arrivals.csv --ready-file %s > %s/receiver.log 2>&1 &"
               % (TRAFFIC, tdir, rx_ready, tdir))
        wait_file(rx_ready)
        hs.cmd("python3 %s send --dst %s --pps %g --duration %g --ready-file %s "
               "> %s/sender.log 2>&1 &"
               % (TRAFFIC, topo.host_ip(dst), args.pps, args.duration, tx_ready, tdir))
        wait_file(tx_ready)
        time.sleep(args.cut_at)
        t0, t1 = tb.inject(topo, failure, up, down)
        time.sleep(max(0.0, args.duration - args.cut_at) + 0.8)
        hd.cmd("pkill -TERM -f 'traffic.py recv'")
        time.sleep(0.5)
        hs.cmd("pkill -TERM -f 'traffic.py send'")

        meta = dict(topology=topo.name, scheme=scheme, bfd_ms=bfd, failure=failure,
                    control_delay_ms=cdelay, trial=trial, src=src, dst=dst,
                    cut=[up, down], cut_labels=[topo.label(up), topo.label(down)],
                    t_fail=t0, t_fail_after=t1, pps=args.pps, duration=args.duration,
                    cut_at=args.cut_at, link_delay_ms=args.link_delay_ms,
                    primary=path(topo, topo.hosts[src], topo.hosts[dst]),
                    reroute_optimal=path(topo, topo.hosts[src], topo.hosts[dst], {(up, down)}))
        with open(os.path.join(tdir, "meta.json"), "w") as fh:
            json.dump(meta, fh, indent=1)
        if state is not None:
            with open(os.path.join(cfg_dir, "ovs_state.json"), "w") as fh:
                json.dump(state, fh, indent=1)
        return True
    finally:
        tb.clear_control_delay()
        ctrl.stop()
        tb.repair(topo, failure, up, down)
        for f in (".rx", ".tx", ".ready"):
            p = os.path.join(tdir, f)
            if os.path.exists(p):
                os.remove(p)


def run_experiment(name, args):
    spec = EXPERIMENTS[name]
    topo = topologies.get(spec["topology"])
    trials = args.trials or (QUICK[name] if args.quick else spec["trials"])
    args.link_delay_ms = spec["link_delay_ms"]
    out = os.path.join(args.results_dir, name)
    if os.path.isdir(out) and not args.keep:
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "experiment.json"), "w") as fh:
        json.dump(dict(spec, name=name, trials=trials, pps=args.pps,
                       description=topo.description), fh, indent=1)

    print("\n=== %s on %s: %d configs x %d controller delays x %d trials ==="
          % (name, topo.name, len(spec["configs"]), len(spec["control_delays"]), trials))
    net = tb.build(topo, link_delay_ms=spec["link_delay_ms"])
    try:
        for cdelay in spec["control_delays"]:
            for scheme, bfd, failure in spec["configs"]:
                cid = config_id(scheme, bfd, failure, cdelay)
                cfg_dir = os.path.join(out, cid)
                os.makedirs(cfg_dir, exist_ok=True)
                tb.set_bfd(topo, bfd)
                if bfd:
                    tb.wait_bfd_up(topo)
                ok = 0
                for trial in range(1, trials + 1):
                    try:
                        ok += bool(run_trial(net, topo, scheme, bfd, failure, cdelay,
                                             trial, cfg_dir, args))
                    except Exception as exc:
                        print("   %s trial %d FAILED: %s" % (cid, trial, exc))
                print("  %-40s %d/%d ok" % (cid, ok, trials))
    finally:
        tb.clear_control_delay()
        try:
            tb.set_bfd(topo, None)
        except Exception:
            pass
        net.stop()
        tb.mn_cleanup()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", default="all", help="e1|e2|e3|e4|all or comma list")
    ap.add_argument("--trials", type=int, default=None)
    ap.add_argument("--quick", action="store_true", help="few trials, for a first check")
    ap.add_argument("--pps", type=float, default=5000)
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--cut-at", type=float, default=2.0)
    ap.add_argument("--settle", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=2014)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--results-dir", default=os.path.join(ROOT, "results"))
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run as root inside the container: ./run.sh suite ...")
    setLogLevel("warning")
    names = list(EXPERIMENTS) if args.exp == "all" else args.exp.split(",")
    for n in names:
        run_experiment(n.strip(), args)
    print("\nDone. Next: ./run.sh analyze")


if __name__ == "__main__":
    main()
