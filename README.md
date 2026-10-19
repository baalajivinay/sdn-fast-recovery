# SDN Fast Link-Failure Recovery: Replicating van Adrichem et al. (2014)

This project replicates **N. L. M. van Adrichem, B. J. van Asten, F. A. Kuipers, "Fast Recovery in
Software-Defined Networks", EWSDN 2014** ([DOI 10.1109/EWSDN.2014.13](https://doi.org/10.1109/EWSDN.2014.13))
on Mininet + Open vSwitch. It compares that paper's method against the other major recovery
techniques on one testbed, with the same failures for every technique.

| Scheme | What happens when a link fails | Controller on the critical path? |
|---|---|---|
| **Restoration** (reactive) | switch reports PORT_STATUS → controller recomputes → pushes new rules | yes |
| **Protection, LoS** | OpenFlow 1.3 fast-failover group switches buckets when carrier drops | no |
| **Protection + BFD** (the paper) | as above, but BFD (1/5/15 ms) declares the link dead; crankback when no local backup | no |
| **Hybrid** (the paper's full proposal) | protection + BFD, then the controller re-optimises off the detour | only after recovery |

## Quick start (on the laptop)

```bash
./run.sh check      # Docker, openvswitch module, host OVS conflict, leftover netem
./run.sh build      # reuses the old sdn-recovery image if present, so it takes seconds
./run.sh test       # offline tests of routing and controller (no Mininet)
./run.sh smoke      # 1 trial of every E1 config, about 2 minutes. Run this first.
./run.sh quick      # every experiment, few trials, about 20 min  -> results/quick/
./run.sh full       # every experiment, full trials, about 1 h    -> results/
```

Each run writes `summary.json`, a printed table and PNG figures in `<results>/figures/`.
`./run.sh sim` produces **model-based draft** results in `results_sim/` with the same files. They
are stamped "SIMULATED" and are only placeholders until the real run is done.

> If `./run.sh check` says `ovs-vswitchd` is running on the host, stop it for the run with
> `sudo systemctl stop openvswitch-switch`. Two vswitchd instances share one kernel datapath.

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
the recovering switch's view: OVS took **4.7 s** to notice by loss of signal.

## Layout

```
sdnrec/        topologies.py   triangle, ring5, usnet (shared by everything)
               routing.py      forwarding state per scheme + packet walk (no OpenFlow)
               controller.py   os-ken app: restoration | protection | hybrid
experiments/   run_suite.py    experiment driver (Mininet)      suite_defs.py  the matrix
               testbed.py      build net, BFD, failures, netem  traffic.py     probe stream
               analyze.py      traces -> summary.json            figures.py     PNG figures
sim/           simulate.py     model-based draft results, same file format
tests/         exhaustive routing test, controller test with fake switches
docs/          REPLICATION.md  paper-to-testbed mapping, deviations, threats to validity
```

## How recovery is measured

A sequence-numbered UDP stream at 5000 pps (0.2 ms resolution) runs between the flow's
endpoints. The outage is the largest gap in the received sequence that ends after the cut.
Recovery time is the number of missing packets times 0.2 ms, the same loss-based measure the
paper uses. A gap-based estimate cross-checks it. Per-packet latency shows the detour path
and the moment re-optimisation happens. Controller milestones come from its event log, which
uses the same clock.

## Built on lessons from the previous project

`~/sdn-failure-recovery`, which replicated Sharma et al. 2013, contributed the container
recipe, the controller-distance emulation, and the "largest gap that ends after the cut"
analysis rule. This project fixes its stated limitations: no BFD, ingress-link failures only,
no crankback, a 6-node topology, and a single detection mechanism.

One correction: its `LIMITATIONS.md` §2 says stock OVS cannot drive fast-failover from BFD. In
OVS 2.17, `may_enable_port()` in `ofproto/ofproto-dpif.c` does make BFD gate port liveness,
which fast-failover buckets watch. OVS also clears `OFPPS_LIVE` and sends PORT_STATUS
(`ofproto/ofproto.c`). van Adrichem's repository notes the feature was merged in October 2014.
