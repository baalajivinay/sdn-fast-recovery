# Replication notes: van Adrichem, van Asten & Kuipers (EWSDN 2014)

## 1. What the paper did

These details were checked against the accepted author manuscript in the TU Delft repository.

- **Problem.** Loss-of-signal (LoS) detection is far too slow on real Ethernet. They measured
  4759 ms average in Open vSwitch and 1697 ms on a Pica8 switch. Sub-50 ms recovery therefore
  needs a probing detector.
- **Method.** BFD runs on every link (per link, not per path, so the number of sessions grows
  with links rather than flows). Primary and backup next hops are preinstalled as OpenFlow
  fast-failover groups. When no local alternative exists, packets *crank back* toward a node
  that has one. The controller later recomputes optimal paths.
- **Detection bound.** Worst case is (M+1)·T with M = 3. They used T = 1, 5 and 15 ms, with a
  1 ms floor set by the implementation.
- **Testbed.** 24 servers running OVS on Ubuntu 13.10 with 1 Gb/s links, plus 5 Pica8 switches.
  BFD was tested on OVS only, using a patched OVS.
- **Failure.** `ifdown` on the switch *opposite* the one that recovers.
- **Measurement.** pktgen at a 0.05 ms interval with sequence numbers. Recovery = missing
  packets × interval.

| Topology | BFD 1 ms | BFD 5 ms | BFD 15 ms |
|---|---|---|---|
| Simple (triangle) | 3.4 ± 0.7 | 15.0 ± 2.2 | 42.1 ± 5.0 |
| Ring (crankback, cut C-D) | 3.3 ± 0.8 | 15.4 ± 2.7 | 46.2 ± 5.1 |
| USNET (24 nodes, 43 links) | – | 13.6 ± 2.6 (>3400 samples) | – |

## 2. Mapping to this testbed

| Paper | Here | Note |
|---|---|---|
| Patched OVS (April 2014 snapshot) | Stock OVS 2.17.5 | BFD gating of fast-failover liveness was merged upstream in Oct 2014 (stated in the authors' repository). Verified in the 2.17 source: `may_enable_port()` and `ofproto_port_set_enable()`. |
| Physical links, `ifdown` on the far switch | veth pairs, **silent** failure (tc ingress drop on both ends, carrier stays up) | On veth, downing the far end drops the near end's carrier instantly, which physical NICs did not do (4.7 s LoS). Silent failure reproduces what the recovering switch experiences: no carrier signal. The *carrier* variant is also run, as the best case for LoS. |
| pktgen, 0.05 ms | Python UDP, 0.2 ms (5000 pps) | Resolution is 4× coarser. Still fine enough for 3 / 15 / 45 ms. |
| Crankback (mechanism not fully specified) | Fast-failover bucket 2 pushes a VLAN tag naming the failed link; every switch on the detour has a tag-specific rule; the egress switch pops it | Loop-free by construction. Tested for all 43 USNET links × 552 host pairs (`tests/test_routing.py`). Reproduces the paper's C→B→A→E path exactly. |
| "20 shortest paths East→West" | The 20 node pairs farthest apart by km | No node names in the edge list used. Farthest pairs are coast-to-coast in a continental backbone. |
| USNET edge list (not published in the paper) | FUSION simulator `data/raw/us_network.txt`: 24 nodes, 43 links, km lengths | Matches the paper's Table I size. One link (6-7) is listed with 900/1150 km; the smaller is used. |
| No restoration baseline reported | Restoration added (same BFD or LoS detection) | Needed to answer "which technique is best". |
| Controller re-optimisation mentioned | **Hybrid** scheme implemented and timed | Shows the detour, then traffic returning to the shortest path. |
| Controller placement not discussed | 10 ms one-way by default; swept 0–50 ms in E4 | netem on loopback delays only OpenFlow traffic. Host traffic crosses veth pairs in namespaces. |

## 3. Expected outcome (hypotheses fixed before running)

1. Protection + BFD tracks the BFD bound: recovery ≈ (M−1)·T … M·T plus about 1–2 ms of OVS
   overhead. This should come within the paper's error bars at 1 and 5 ms.
2. With a silent failure, every LoS-based scheme (restoration or protection) never recovers.
   Detection is the binding constraint, not the rerouting mechanism.
3. Crankback costs no extra recovery time, but it adds path stretch until the controller
   re-optimises (hybrid).
4. Restoration ≈ detection + 2 × controller distance + processing. It crosses 50 ms at a
   modest controller distance, while protection stays flat.
5. On USNET, BFD 5 ms protection lands near the paper's 13.6 ms.

## 4. Threats to validity

- **Software switch.** Absolute numbers are emulated. Ratios and which side of 50 ms each
  scheme lands on are the claims. Real ASICs run BFD in hardware at 3.3 ms and below.
- **BFD at 1 ms in userspace.** ovs-vswitchd runs BFD in its main loop. On USNET (86 sessions)
  1 ms can overload it, which is why the paper used 5 ms there, and so does E3.
- **Shared CPU.** The sender spins one core, and OVS, the controller and the receiver share the
  rest. Check `sender.log` for `late=` counts. An occasional late packet does not count as loss.
- **Controller distance via loopback netem** delays *all* host loopback traffic while a run is
  active. The entrypoint and driver both remove it. If a run is killed, clear it with
  `sudo tc qdisc del dev lo root`.
- **Static topology.** Neither controller discovers links (LLDP timers would dominate
  restoration). This favours restoration, the slower scheme, so the bias works against the
  headline result.
- **Single failures only.** Hybrid re-protects after the first failure (tested offline for a
  second failure), but the suite measures one cut per trial.
