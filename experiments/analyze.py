#!/usr/bin/env python3
"""
Turn raw traces into recovery metrics, results/summary.json and figures.

    python3 experiments/analyze.py [--results-dir results]

Per trial (from arrivals.csv + meta.json + controller_events.jsonl):
  recovery_ms   loss-based: packets missing in the outage gap x send interval
                (the measure the paper uses)
  gap_ms        send-time distance across the gap minus one interval; should
                agree with recovery_ms to within one interval
  recovered     False if no packet got through for the rest of the run
  extra_loss    packets lost after the main outage (e.g. while the hybrid
                controller rewrites tables)
  ctrl_*_ms     controller milestones relative to the cut
  lat_before / lat_after   median one-way latency before the cut and once
                the network has settled -- shows crankback detours

The outage is the largest gap that *ends after* the cut. Anchoring on the
packet pair that straddles the cut timestamp under-reports: the link goes
down a fraction of a millisecond after the timestamp, so that pair is
usually intact (lesson recorded in the previous project's RESULTS.md).
"""

import argparse
import csv
import glob
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAPER = {
    # van Adrichem et al. 2014, mean +- sd in ms (verified against the
    # accepted manuscript, TU Delft repository)
    "triangle": {1: (3.4, 0.7), 5: (15.0, 2.2), 15: (42.1, 5.0)},
    "ring5": {1: (3.3, 0.8), 5: (15.4, 2.7), 15: (46.2, 5.1)},
    "usnet": {5: (13.6, 2.6)},
    "los_ovs_ms": 4759, "los_pica8_ms": 1697, "admin_down_ms": 50.9,
}


def load_trial(tdir):
    with open(os.path.join(tdir, "meta.json")) as fh:
        meta = json.load(fh)
    rows = []
    with open(os.path.join(tdir, "arrivals.csv")) as fh:
        for r in csv.DictReader(fh):
            rows.append((int(r["seq"]), float(r["send_time"]), float(r["recv_time"])))
    rows.sort()
    events = []
    ev_path = os.path.join(tdir, "controller_events.jsonl")
    if os.path.exists(ev_path):
        with open(ev_path) as fh:
            events = [json.loads(l) for l in fh if l.strip()]
    return meta, rows, events


def trial_metrics(meta, rows, events):
    pps = meta["pps"]
    iv = 1.0 / pps
    tf = meta["t_fail"]
    total = int(meta["duration"] * pps)
    m = {"trial": meta["trial"], "cut": meta["cut_labels"], "src": meta["src"],
         "dst": meta["dst"]}
    if len(rows) < 10:
        m.update(valid=False, reason="too few packets")
        return m
    seqs = [r[0] for r in rows]
    # schedule origin from the first packet: send(seq) ~= origin + seq*iv
    origin = rows[0][1] - rows[0][0] * iv
    seq_fail = int((tf - origin) / iv)

    gaps = []
    for (a, sa, _), (b, sb, _) in zip(rows, rows[1:]):
        if b - a > 1 and sb > tf:
            gaps.append((b - a - 1, a, b, sa, sb))
    last_send = rows[-1][1]
    end_of_run = origin + total * iv
    # Recovered = traffic is still arriving at the end of the run. Packets
    # already in flight when the cut happened can arrive just after it, so
    # "anything after the cut" is not enough.
    recovered = last_send > tf and (end_of_run - last_send) < 0.25
    received = set(seqs)
    lost_after = sum(1 for s in range(max(seq_fail, 0), total) if s not in received)
    if not recovered:
        m.update(valid=True, recovered=False, recovery_ms=None, gap_ms=None,
                 lost=lost_after, extra_loss=0)
    elif not gaps:
        m.update(valid=True, recovered=True, recovery_ms=0.0, gap_ms=0.0, lost=lost_after,
                 extra_loss=lost_after)
    else:
        main = max(gaps, key=lambda g: g[0])
        missing, a, b, sa, sb = main
        m.update(valid=True, recovered=True, recovery_ms=missing * iv * 1e3,
                 gap_ms=max(0.0, (sb - sa - iv) * 1e3), lost=lost_after,
                 extra_loss=lost_after - missing,
                 outage_start_ms=(sa - tf) * 1e3)

    lat = [((s - tf), (r - s) * 1e3) for _, s, r in rows]
    before = [l for t, l in lat if -1.0 < t < -0.05]
    settle = [l for t, l in lat if t > 1.0]
    m["lat_before_ms"] = st.median(before) if before else None
    m["lat_after_ms"] = st.median(settle) if settle else None
    # timeline sample around the cut, for plots
    tl = [(round(t * 1e3, 2), round(l, 3)) for t, l in lat if -0.1 < t < 0.6]
    m["timeline"] = tl[::max(1, len(tl) // 1500)]

    def first(name):
        for e in events:
            if e.get("ev") == name and e["t"] >= tf - 0.01:
                return (e["t"] - tf) * 1e3
        return None
    m["ctrl_detect_ms"] = first("failure_detected")
    m["ctrl_restored_ms"] = first("restoration_complete")
    m["ctrl_reopt_ms"] = first("reopt_complete")
    m["of_errors"] = sum(1 for e in events if e.get("ev") == "of_error")
    return m


def describe(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(s), "mean": st.mean(s), "sd": st.stdev(s) if len(s) > 1 else 0.0,
            "median": st.median(s), "min": s[0], "max": s[-1],
            "p95": s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]}


def analyze(results_dir):
    simulated = os.path.exists(os.path.join(results_dir, "SIMULATED"))
    summary = {"source": "simulated" if simulated else "measured", "paper": PAPER,
               "experiments": {}}
    for exp_dir in sorted(glob.glob(os.path.join(results_dir, "e*"))):
        if not os.path.isdir(exp_dir):
            continue
        name = os.path.basename(exp_dir)
        with open(os.path.join(exp_dir, "experiment.json")) as fh:
            spec = json.load(fh)
        exp = {"spec": spec, "configs": {}}
        sys.path[:0] = [ROOT, os.path.join(ROOT, "experiments")]
        from suite_defs import config_id
        order = [config_id(sc, b, f, cd) for cd in spec["control_delays"]
                 for sc, b, f in spec["configs"]]
        for cfg_dir in sorted(glob.glob(os.path.join(exp_dir, "*_cd*")),
                              key=lambda d: (order.index(os.path.basename(d))
                                             if os.path.basename(d) in order else 999, d)):
            trials = []
            meta0 = None
            for tdir in sorted(glob.glob(os.path.join(cfg_dir, "trial*"))):
                if not os.path.exists(os.path.join(tdir, "meta.json")):
                    continue
                try:
                    meta, rows, events = load_trial(tdir)
                except FileNotFoundError:
                    continue
                meta0 = meta0 or meta
                trials.append(trial_metrics(meta, rows, events))
            if not meta0:
                continue
            valid = [t for t in trials if t.get("valid")]
            state = None
            sp = os.path.join(cfg_dir, "ovs_state.json")
            if os.path.exists(sp):
                with open(sp) as fh:
                    per_sw = json.load(fh)
                state = {"flows": sum(v["flows"] for v in per_sw.values()),
                         "groups": sum(v["groups"] for v in per_sw.values()),
                         "max_per_switch": max(v["flows"] + v["groups"] for v in per_sw.values())}
            exp["configs"][os.path.basename(cfg_dir)] = {
                "scheme": meta0["scheme"], "bfd_ms": meta0["bfd_ms"],
                "failure": meta0["failure"], "control_delay_ms": meta0["control_delay_ms"],
                "n": len(valid),
                "recovered": sum(1 for t in valid if t["recovered"]),
                "recovery_ms": describe([t["recovery_ms"] for t in valid]),
                "gap_ms": describe([t["gap_ms"] for t in valid]),
                "extra_loss": describe([t["extra_loss"] for t in valid]),
                "ctrl_detect_ms": describe([t["ctrl_detect_ms"] for t in valid]),
                "ctrl_restored_ms": describe([t["ctrl_restored_ms"] for t in valid]),
                "ctrl_reopt_ms": describe([t["ctrl_reopt_ms"] for t in valid]),
                "lat_before_ms": describe([t["lat_before_ms"] for t in valid]),
                "lat_after_ms": describe([t["lat_after_ms"] for t in valid]),
                "of_errors": sum(t.get("of_errors", 0) for t in valid),
                "state": state,
                "trials": [{k: v for k, v in t.items() if k != "timeline"} for t in trials],
                "timeline": valid[0]["timeline"] if valid else [],
            }
        summary["experiments"][name] = exp
    return summary


def print_table(summary):
    for name, exp in summary["experiments"].items():
        print("\n== %s (%s) ==" % (name, exp["spec"]["topology"]))
        print("  %-40s %5s %9s %20s %10s" % ("config", "n", "recovered", "recovery ms (mean±sd)",
                                            "extra loss"))
        for cid, c in exp["configs"].items():
            r = c["recovery_ms"]
            rs = "%.2f ± %.2f" % (r["mean"], r["sd"]) if r else "never"
            el = c["extra_loss"]["mean"] if c["extra_loss"] else 0
            print("  %-40s %5d %4d/%-4d %20s %10.1f" % (cid, c["n"], c["recovered"], c["n"], rs, el))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default=os.path.join(ROOT, "results"))
    ap.add_argument("--no-figures", action="store_true")
    a = ap.parse_args()
    summary = analyze(a.results_dir)
    if not summary["experiments"]:
        sys.exit("no experiments found under %s" % a.results_dir)
    out = os.path.join(a.results_dir, "summary.json")
    with open(out, "w") as fh:
        json.dump(summary, fh, indent=1)
    print_table(summary)
    print("\nwrote", out)
    if not a.no_figures:
        sys.path.insert(0, os.path.join(ROOT, "experiments"))
        import figures
        for f in figures.make_all(summary, os.path.join(a.results_dir, "figures")):
            print("wrote", f)


if __name__ == "__main__":
    main()
