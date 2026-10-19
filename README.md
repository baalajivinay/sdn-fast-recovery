# SDN Fast Link-Failure Recovery: Replicating van Adrichem et al. (2014)

This project replicates **N. L. M. van Adrichem, B. J. van Asten, F. A. Kuipers, "Fast Recovery in
Software-Defined Networks", EWSDN 2014** ([DOI 10.1109/EWSDN.2014.13](https://doi.org/10.1109/EWSDN.2014.13))
on Mininet + Open vSwitch. It compares that paper's method against the other major recovery
techniques on one testbed, with the same failures for every technique.

| Scheme | What happens when a link fails | Controller on the critical path? |
|---|---|---|
| **Restoration** (reactive) | switch reports PORT_STATUS → controller recomputes → pushes new rules | yes |
| **Protection, LoS** | OpenFlow fast-failover group switches buckets when carrier drops | no |
| **Protection + BFD** (the paper) | as above, but BFD (1/5/15 ms) declares the link dead; crankback when no local backup | no |
| **Hybrid** (the paper's full proposal) | protection + BFD, then the controller re-optimises off the detour | only after recovery |

## Results

Measured on Mininet 2.3, Open vSwitch 2.17.5 and os-ken, 325 trials. Controller 10 ms away
(one-way) unless stated. All times in ms.

**Protection + BFD vs the paper** (BFD interval 1 / 5 / 15 ms)

| Topology | Paper | This replication |
|---|---|---|
| Triangle | 3.4 / 15.0 / 42.1 | 3.6 / 14.7 / 40.9 (median) |
| 5-node ring, crankback | 3.3 / 15.4 / 46.2 | 4.8 / 17.7 / 40.7 (median) |
| USNET, 24 nodes, BFD 5 ms | 13.6 ± 2.6 | 16.3 ± 2.9 |

**All schemes, triangle** (BFD 5 ms)

| Scheme | Silent failure | Carrier loss |
|---|---|---|
| Restoration | 34.7 ± 1.1 | 23.8 ± 1.9 |
| Protection, LoS | never recovers (0/10) | 2.1 ± 0.3 |
| Protection + BFD | 15.3 | – |
| Hybrid (ring) | 15.7, re-optimised at 47.9 | – |

**Other findings**

- Controller distance 0 → 50 ms: restoration rises from 21.5 to 118.0 ms. Protection and
  hybrid stay at 14–17 ms.
- State cost on USNET: protection needs 3,293 flows + groups against 576 for restoration (5.7×).
- BFD at 1 ms is fragile in software OVS: 3 of 20 triangle and ring trials never fired or
  flapped, so medians are quoted there.
- The first full run exposed a crankback bug: OpenFlow drops a packet sent back out its own
  ingress port. Crankback buckets now use `OFPP_IN_PORT` (commit `37f26b4`), and every
  experiment was re-run. Neither the offline tests nor the simulator caught it.

Full numbers are in [`results/summary.json`](results/summary.json) and the figures in
[`results/figures/`](results/figures/).

## Replicate it (one container)

Everything needed is in one Docker image: Mininet 2.3, Open vSwitch 2.17.5, os-ken 4.2.1, the
pinned Python packages of the measured run, and the experiment code. No clone needed.

**Host requirements:** Linux, Docker, and the `openvswitch` kernel module (shipped with most
distributions; the container loads it itself through `-v /lib/modules`). Stop any host Open
vSwitch for the run (`sudo systemctl stop openvswitch-switch`), since two `ovs-vswitchd`
instances share one kernel datapath.

```bash
IMG=ghcr.io/baalajivinay/sdn-fast-recovery:v1.0
mkdir -p results
docker run --rm -it --privileged -v /lib/modules:/lib/modules:ro \
    -e HOST_UID=$(id -u) -e HOST_GID=$(id -g) \
    -v "$PWD/results:/work/results" $IMG smoke
```

Swap `smoke` for any command below. Running the image with no command prints this list.

| Command | What it does | Time |
|---|---|---|
| `check` | is the openvswitch kernel module available? | seconds |
| `test` | offline tests of routing and controller (needs no `--privileged`) | seconds |
| `smoke` | 1 trial of every E1 config → `results/smoke/`. Run this first. | ~1–2 min |
| `quick` | every experiment, few trials → `results/quick/` | ~20 min |
| `full` | every experiment, full trials → `results/` (the published run) | ~1 h |
| `suite …` | `run_suite.py` directly, e.g. `suite --exp e3 --trials 50` | |
| `analyze [dir]` | recompute summary + figures | seconds |
| `shell` | bash inside the testbed | |

Each run writes `summary.json`, a printed table and PNG figures. The published numbers are
inside the image at `/work/reference/summary.json` for comparison. `sim` writes model-based
placeholder results to `results_sim/`, stamped "SIMULATED"; they are not measurements.

**Building the image yourself:** `docker build -t sdn-fast-recovery .` from a clone. The base
image is pinned by digest and Python packages by `docker/constraints.txt`.

### Working on the code

`./run.sh <command>` runs the same commands with the repo mounted over `/work`, so edits take
effect without a rebuild. `./run.sh check` also checks for a host `ovs-vswitchd` and a
leftover loopback delay, and `./run.sh build` builds the local image.

## Interactive simulator

`app/` holds a self-contained visual model of the four schemes, used for the slide
illustrations. It is a teaching aid; the numbers above come from the Mininet runs.

- **Linux executable:** download `SDN_Recovery_Simulator` from the
  [Releases](../../releases) page, then `chmod +x SDN_Recovery_Simulator && ./SDN_Recovery_Simulator`.
- **From source:** `python3 sdn_app.py` (uses `pywebview` if installed, else your browser),
  or just open `app/index.html`.
- **Rebuild the executable:** `./build_app.sh` (PyInstaller) → `dist/SDN_Recovery_Simulator`.

## Experiments

| ID | Topology | Question | Paper reference |
|---|---|---|---|
| E1 | triangle A-B-C (Fig. 3a) | How much does detection matter? LoS vs BFD 1/5/15 ms, carrier vs silent failure, restoration vs protection | 3.4 / 15.0 / 42.1 ms |
| E2 | 5-node ring (Fig. 3b), cut C-D | Interior failure: does crankback work and what does it cost? Adds restoration and hybrid | 3.3 / 15.4 / 46.2 ms |
| E3 | USNET, 24 nodes / 43 links (Fig. 3c) | Scale: random cut on one of 20 long paths | 13.6 ± 2.6 ms at BFD 5 ms |
| E4 | ring | Controller distance 0–50 ms: who pays for it? | (extension) |

**Two failure types.** A *carrier* failure brings the far-end interface down, so the near end
sees loss of signal immediately. That is the best case for LoS. A *silent* failure drops every
frame in both directions while carrier stays up, as with a fibre cut behind a transceiver or a
line-card fault. Only BFD can see it. The paper's physical-NIC failures behaved like this from
the recovering switch's view: OVS took about **4.8 s** to notice by loss of signal.

## Layout

```
sdnrec/        topologies.py   triangle, ring5, usnet (shared by everything)
               routing.py      forwarding state per scheme + packet walk (no OpenFlow)
               controller.py   os-ken app: restoration | protection | hybrid
experiments/   run_suite.py    experiment driver (Mininet)      suite_defs.py  the matrix
               testbed.py      build net, BFD, failures, netem  traffic.py     probe stream
               analyze.py      traces -> summary.json            figures.py     PNG figures
tests/         exhaustive routing test, controller test with fake switches
Dockerfile     the replication image (code + pinned environment)
docker/        entrypoint.sh (command menu), constraints.txt (pinned Python packages)
results/       summary.json and figures from the measured run
sim/           simulate.py     model-based placeholder results, same file format
app/           index.html      interactive simulator; media/ has demo recordings
sdn_app.py     desktop launcher for the simulator; build_app.sh packages it
slides/        presentation.tex / .pdf (Beamer), talk_script.txt (10-minute, 4 speakers)
docs/          REPLICATION.md  paper-to-testbed mapping, deviations, threats to validity
               survey.html     visual summary of the field and of these results
```

## How recovery is measured

A sequence-numbered UDP stream at 5000 pps (0.2 ms resolution) runs between the flow's
endpoints. The outage is the largest gap in the received sequence that ends after the cut.
Recovery time is the number of missing packets times 0.2 ms, the same loss-based measure the
paper uses. A gap-based estimate cross-checks it. Per-packet latency shows the detour path
and the moment re-optimisation happens. Controller milestones come from its event log, which
uses the same clock.

## Notes

This builds on an earlier replication of Sharma et al. 2013, which contributed the container
recipe, the controller-distance emulation, and the "largest gap that ends after the cut"
analysis rule. This project adds what that one lacked: BFD, interior-link failures, crankback,
a 24-node topology, and more than one detection mechanism.

Stock OVS 2.17 can drive fast-failover from BFD: `may_enable_port()` in
`ofproto/ofproto-dpif.c` makes BFD gate port liveness, which fast-failover buckets watch, and
OVS clears `OFPPS_LIVE` and sends PORT_STATUS (`ofproto/ofproto.c`). van Adrichem's repository
notes the feature was merged in October 2014.
