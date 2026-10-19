"""
Static figures (PNG) for slides and the report, drawn from summary.json.

Colour follows the scheme everywhere, so a reader learns it once:
restoration = orange, protection = blue, hybrid = aqua; the paper's
published numbers are always hollow grey markers.
"""

import os
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

COL = {"restoration": "#eb6834", "protection": "#2a78d6", "hybrid": "#1baf7a"}
PAPER_C = "#52514e"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BFD_SHADE = {None: "#9ec5f4", 1: "#184f95", 5: "#2a78d6", 15: "#86b6ef"}

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlecolor": INK,
    "axes.titlelocation": "left", "legend.frameon": False, "lines.linewidth": 2,
})


def _label(c):
    det = "BFD %d ms" % c["bfd_ms"] if c["bfd_ms"] else "LoS"
    return "%s · %s · %s failure" % (c["scheme"], det, c["failure"])


def _save(fig, outdir, name, made):
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, name)
    fig.savefig(p, dpi=160, bbox_inches="tight")
    plt.close(fig)
    made.append(p)


def _src_note(summary):
    return "SIMULATED (model) - replace by running the suite" \
        if summary.get("source") == "simulated" else "measured on Mininet + OVS 2.17"


def fig_detection(summary, outdir, made):
    exp = summary["experiments"].get("e1")
    if not exp:
        return
    cfgs = list(exp["configs"].values())
    fig, ax = plt.subplots(figsize=(8.5, 0.48 * len(cfgs) + 1.4))
    ys = list(range(len(cfgs)))[::-1]
    xmax = max([c["recovery_ms"]["max"] for c in cfgs if c["recovery_ms"]] + [10])
    for y, c in zip(ys, cfgs):
        r = c["recovery_ms"]
        if r and c["recovered"] == c["n"]:
            ax.barh(y, r["mean"], height=0.5, color=COL[c["scheme"]], alpha=0.95,
                    xerr=r["sd"], error_kw={"ecolor": INK2, "lw": 1, "capsize": 3})
            ax.text(r["mean"] + r["sd"] + xmax * 0.015, y, "%.1f ms" % r["mean"],
                    va="center", color=INK, fontsize=9)
        else:
            ax.text(xmax * 0.01, y, "never recovers  (%d/%d)" % (c["recovered"], c["n"]),
                    va="center", color=INK2, fontsize=9, style="italic")
    ax.set_axisbelow(True)
    ax.grid(axis="y", visible=False)
    ax.set_yticks(ys)
    ax.set_yticklabels([_label(c) for c in cfgs])
    ax.set_xlabel("recovery time (ms)")
    ax.set_xlim(0, xmax * 1.25)
    ax.axvline(50, color=INK2, lw=1)
    ax.text(50, len(cfgs) - 0.4, " 50 ms", color=INK2, fontsize=8, va="bottom")
    ax.set_title("E1 · Detection decides recovery (triangle, controller 10 ms away)")
    fig.text(0.01, -0.02, _src_note(summary), color=INK2, fontsize=8)
    _save(fig, outdir, "fig1_detection.png", made)


def fig_bfd_vs_paper(summary, outdir, made):
    panels = [("e1", "triangle", "Triangle (Fig. 3a)"), ("e2", "ring5", "Ring, crankback (Fig. 3b)")]
    panels = [p for p in panels if p[0] in summary["experiments"]]
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(5.2 * len(panels), 4), sharey=True,
                             squeeze=False)
    for ax, (eid, topo, title) in zip(axes[0], panels):
        exp = summary["experiments"][eid]
        # Median and interquartile range: at T = 1 ms a single BFD flap (~1 s)
        # dominates the mean and sd, which says nothing about typical recovery.
        pts = []
        for c in exp["configs"].values():
            if c["scheme"] == "protection" and c["bfd_ms"] and c["failure"] == "silent":
                v = [tr["recovery_ms"] for tr in c.get("trials", []) if tr.get("recovered")]
                if len(v) >= 2:
                    q1, med, q3 = statistics.quantiles(v, n=4)
                    pts.append((c["bfd_ms"], statistics.median(v), q1, q3))
        pts.sort()
        xs = [1, 5, 15]
        ax.fill_between(xs, [2 * x for x in xs], [3 * x for x in xs], color=COL["protection"],
                        alpha=0.10, lw=0, label="BFD theory: (M-1)·T to M·T, M=3")
        if pts:
            ax.errorbar([p[0] for p in pts], [p[1] for p in pts],
                        yerr=[[p[1] - p[2] for p in pts], [p[3] - p[1] for p in pts]],
                        color=COL["protection"], marker="o", ms=7, capsize=3,
                        label="this replication (median, IQR)", mec=SURF, mew=2)
        paper = summary["paper"].get(topo, {})
        px = sorted(int(k) for k in paper)
        ax.errorbar([x * 1.06 for x in px], [paper[str(x)][0] if str(x) in paper else paper[x][0]
                                             for x in px],
                    yerr=[paper[str(x)][1] if str(x) in paper else paper[x][1] for x in px],
                    color=PAPER_C, marker="s", ms=7, mfc="none", ls="none", capsize=3,
                    label="van Adrichem et al. 2014 (mean ± sd)")
        ax.axhline(50, color=INK2, lw=1)
        ax.set_xticks(xs)
        ax.set_xlabel("BFD transmit interval T (ms)")
        ax.set_title(title)
    axes[0][0].set_ylabel("recovery time (ms)")
    h, l = axes[0][0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, fontsize=9, bbox_to_anchor=(0.5, -0.08))
    fig.text(0.01, -0.13, _src_note(summary), color=INK2, fontsize=8)
    _save(fig, outdir, "fig2_bfd_vs_paper.png", made)


def fig_timeline(summary, outdir, made):
    exp = summary["experiments"].get("e2")
    if not exp:
        return
    want = [("protection", 5), ("hybrid", 5), ("restoration", 5)]
    rows = []
    for scheme, bfd in want:
        for c in exp["configs"].values():
            if c["scheme"] == scheme and c["bfd_ms"] == bfd and c["timeline"]:
                rows.append(c)
                break
    if not rows:
        return
    fig, axes = plt.subplots(len(rows), 1, figsize=(8.5, 2.1 * len(rows)), sharex=True,
                             squeeze=False)
    for ax, c in zip(axes[:, 0], rows):
        t = [p[0] for p in c["timeline"]]
        l = [p[1] for p in c["timeline"]]
        ax.scatter(t, l, s=3, color=COL[c["scheme"]], lw=0)
        ax.axvline(0, color=INK2, lw=1)
        r = c["recovery_ms"]
        txt = "%s · outage %.1f ms" % (c["scheme"], r["mean"]) if r else c["scheme"]
        if c["ctrl_reopt_ms"]:
            ax.axvline(c["ctrl_reopt_ms"]["mean"], color=COL["hybrid"], lw=1, ls=":")
            txt += " · re-optimised at %.0f ms" % c["ctrl_reopt_ms"]["mean"]
        ax.set_title(txt, fontsize=10)
        ax.set_ylabel("latency (ms)")
        ax.set_ylim(0, max(l) * 1.2 if l else 1)
    axes[-1][0].set_xlabel("time since the C-D cut (ms)")
    fig.suptitle("E2 · What packets experience: gap, crankback detour, re-optimisation",
                 x=0.01, ha="left", fontweight="bold", color=INK)
    fig.text(0.01, -0.02, _src_note(summary) + " · ring, 1 ms per unit weight (E-A = 10 ms)", color=INK2, fontsize=8)
    fig.tight_layout()
    _save(fig, outdir, "fig3_ring_timeline.png", made)


def fig_usnet(summary, outdir, made):
    exp = summary["experiments"].get("e3")
    if not exp:
        return
    fig, ax = plt.subplots(figsize=(7.5, 4))
    styles = {5: "-", 15: "--"}
    for c in exp["configs"].values():
        vals = sorted(t["recovery_ms"] for t in c["trials"] if t.get("recovery_ms") is not None)
        if not vals:
            continue
        ys = [(i + 1) / len(vals) for i in range(len(vals))]
        ax.step(vals, ys, where="post", color=COL[c["scheme"]], ls=styles.get(c["bfd_ms"], "-"),
                label="%s · BFD %s ms" % (c["scheme"], c["bfd_ms"]))
    p = summary["paper"]["usnet"]
    m, sd = (p["5"] if "5" in p else p[5])
    ax.axvspan(m - sd, m + sd, color=PAPER_C, alpha=0.12, lw=0)
    ax.axvline(m, color=PAPER_C, lw=1)
    ax.text(m + sd + 0.6, 0.97, "paper: %.1f ± %.1f ms\n(BFD 5 ms)" % (m, sd), color=PAPER_C,
            fontsize=8, ha="left", va="top")
    ax.axvline(50, color=INK2, lw=1)
    ax.set_xlabel("recovery time (ms)")
    ax.set_ylabel("fraction of failures recovered")
    ax.set_title("E3 · USNET (24 nodes, 43 links): random cuts on 20 long paths")
    ax.legend(loc="lower right", fontsize=8)
    fig.text(0.01, -0.03, _src_note(summary), color=INK2, fontsize=8)
    _save(fig, outdir, "fig4_usnet_cdf.png", made)


def fig_distance(summary, outdir, made):
    exp = summary["experiments"].get("e4")
    if not exp:
        return
    fig, ax = plt.subplots(figsize=(7.5, 4))
    by = {}
    for c in exp["configs"].values():
        if c["recovery_ms"]:
            by.setdefault(c["scheme"], []).append((c["control_delay_ms"], c["recovery_ms"]))
    for scheme, pts in by.items():
        pts.sort()
        ax.errorbar([p[0] for p in pts], [p[1]["mean"] for p in pts],
                    yerr=[p[1]["sd"] for p in pts], color=COL[scheme], marker="o", ms=7,
                    capsize=3, mec=SURF, mew=2, label=scheme)
        if scheme == "restoration":
            ax.annotate("%.0f ms" % pts[-1][1]["mean"], (pts[-1][0], pts[-1][1]["mean"]),
                        xytext=(8, 0), textcoords="offset points", va="center", color=INK,
                        fontsize=9)
    ax.axhline(50, color=INK2, lw=1)
    ax.text(0, 51, "50 ms carrier-grade target", color=INK2, fontsize=8, va="bottom")
    ax.set_xlabel("one-way controller distance (ms)")
    ax.set_ylabel("recovery time (ms)")
    ax.set_title("E4 · Only restoration pays for controller distance (ring, BFD 5 ms)")
    ax.legend(loc="upper left", fontsize=8)
    fig.text(0.01, -0.03, _src_note(summary), color=INK2, fontsize=8)
    _save(fig, outdir, "fig5_controller_distance.png", made)


def fig_state(summary, outdir, made):
    rows = []
    for eid in ("e1", "e2", "e3"):
        exp = summary["experiments"].get(eid)
        if not exp:
            continue
        seen = set()
        for c in exp["configs"].values():
            if c["state"] and c["scheme"] not in seen:
                seen.add(c["scheme"])
                rows.append((exp["spec"]["topology"], c["scheme"],
                             c["state"]["flows"] + c["state"]["groups"]))
    if not rows:
        return
    topos = []
    for t, _, _ in rows:
        if t not in topos:
            topos.append(t)
    order = ["restoration", "protection", "hybrid"]
    fig, axes = plt.subplots(1, len(topos), figsize=(3.4 * len(topos), 3.6), squeeze=False)
    for ax, t in zip(axes[0], topos):
        schemes = [sc for sc in order if any(tt == t and s == sc for tt, s, _ in rows)]
        vals = [next(v for tt, s, v in rows if tt == t and s == sc) for sc in schemes]
        ax.bar(range(len(vals)), vals, width=0.5, color=[COL[s] for s in schemes])
        for i, v in enumerate(vals):
            ax.text(i, v, "%d" % v, ha="center", va="bottom", color=INK, fontsize=9)
        ax.set_axisbelow(True)
        ax.grid(axis="x", visible=False)
        ax.set_xticks(range(len(vals)))
        ax.set_xticklabels(schemes, fontsize=9)
        ax.set_title(t, fontsize=11)
    axes[0][0].set_ylabel("flow + group entries (all switches)")
    fig.suptitle("The price of speed: forwarding state installed before any failure",
                 x=0.01, ha="left", fontweight="bold", color=INK)
    fig.tight_layout()
    _save(fig, outdir, "fig6_state_cost.png", made)


def make_all(summary, outdir):
    made = []
    for f in (fig_detection, fig_bfd_vs_paper, fig_timeline, fig_usnet, fig_distance, fig_state):
        f(summary, outdir, made)
    return made
