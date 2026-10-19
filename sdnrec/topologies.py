"""
Topologies for the van Adrichem et al. (EWSDN 2014) replication.

Every topology is plain data, shared by three consumers that must agree on
it exactly: the Mininet builder (experiments/), the os-ken controller
(sdnrec/controller.py) and the model-based simulator (sim/). Port numbers
are derived deterministically here so that a fast-failover bucket's
watch_port always refers to the link the controller thinks it does.

    triangle  paper Fig. 3a   A-B-C, primary A->B->C, backup A->C
    ring5     paper Fig. 3b   A..E ring, primary A->B->C->D->E;
                              cutting C-D forces crankback C->B->A->E
    usnet     paper Fig. 3c   24 nodes / 43 links (US backbone), used for
                              the scalability run

Port numbering on each switch: host ports first (starting at 1), then one
port per incident link in the order the links are listed.
"""

import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Topology:
    name: str
    switches: list                      # ["s1", "s2", ...]
    links: list                         # [(a, b, weight), ...] undirected
    hosts: dict                         # {"h1": "s1", ...}
    flows: list                         # [(src_host, dst_host), ...] measured flows
    labels: dict = field(default_factory=dict)   # {"s1": "A"} paper names
    link_km: dict = field(default_factory=dict)  # {(a, b): km} geo length, optional
    fixed_failure: tuple = None         # (upstream_sw, downstream_sw) or None=random
    description: str = ""

    # ---- derived, filled by __post_init__ ---------------------------------
    port: dict = field(default_factory=dict, repr=False)       # (a, b) -> port on a
    host_port: dict = field(default_factory=dict, repr=False)  # host -> port on its switch
    link_index: dict = field(default_factory=dict, repr=False) # canonical (a, b) -> idx

    def __post_init__(self):
        nxt = {s: 1 for s in self.switches}
        for h in sorted(self.hosts, key=_natkey):
            s = self.hosts[h]
            self.host_port[h] = nxt[s]
            nxt[s] += 1
        for i, (a, b, _w) in enumerate(self.links):
            self.port[(a, b)] = nxt[a]
            nxt[a] += 1
            self.port[(b, a)] = nxt[b]
            nxt[b] += 1
            self.link_index[canon(a, b)] = i

    # ---- helpers -----------------------------------------------------------
    def dpid(self, sw):
        return int(sw[1:])

    def sw_of_dpid(self, dpid):
        return "s%d" % dpid

    def host_ip(self, h):
        return "10.0.0.%d" % int(h[1:])

    def host_mac(self, h):
        return "00:00:00:00:00:%02x" % int(h[1:])

    def weight(self, a, b):
        return self.links[self.link_index[canon(a, b)]][2]

    def neighbors(self, sw):
        out = []
        for a, b, w in self.links:
            if a == sw:
                out.append((b, w))
            elif b == sw:
                out.append((a, w))
        return sorted(out, key=lambda t: _natkey(t[0]))

    def link_of_port(self, sw, port_no):
        """Inverse of `port`: which canonical link does (sw, port) belong to."""
        for (a, b), p in self.port.items():
            if a == sw and p == port_no:
                return canon(a, b)
        return None

    def tag(self, link):
        """VLAN id used to mark packets detouring around `link` (1..4094)."""
        return self.link_index[canon(*link)] + 1

    def label(self, sw):
        return self.labels.get(sw, sw)

    def switch_links(self):
        return [(a, b) for a, b, _ in self.links]


def canon(a, b):
    return (a, b) if _natkey(a) <= _natkey(b) else (b, a)


def _natkey(name):
    return (name[0], int(name[1:]))


# ---------------------------------------------------------------------------
# Paper topologies
# ---------------------------------------------------------------------------

def triangle():
    # The paper's primary is A->B->C with A->C as the backup, so A-C is made
    # the expensive link; with equal weights Dijkstra would route over it.
    return Topology(
        name="triangle",
        switches=["s1", "s2", "s3"],
        labels={"s1": "A", "s2": "B", "s3": "C"},
        links=[("s1", "s2", 1), ("s2", "s3", 1), ("s1", "s3", 3)],
        hosts={"h1": "s1", "h2": "s3"},
        flows=[("h1", "h2")],
        fixed_failure=("s1", "s2"),
        description="van Adrichem Fig. 3a: primary A-B-C, backup A-C, cut A-B",
    )


def ring5():
    # Primary must go the long way round (A-B-C-D-E) as in the paper, so the
    # closing link E-A carries a high weight. Cutting C-D then leaves C with
    # only one way to E: back through B and A (crankback).
    return Topology(
        name="ring5",
        switches=["s1", "s2", "s3", "s4", "s5"],
        labels={"s1": "A", "s2": "B", "s3": "C", "s4": "D", "s5": "E"},
        links=[("s1", "s2", 1), ("s2", "s3", 1), ("s3", "s4", 1),
               ("s4", "s5", 1), ("s5", "s1", 10)],
        hosts={"h1": "s1", "h2": "s5"},
        flows=[("h1", "h2")],
        fixed_failure=("s3", "s4"),
        description="van Adrichem Fig. 3b: cut C-D, crankback C-B-A-E",
    )


def usnet(n_flows=20):
    """
    USNET, 24 nodes / 43 links -- the size given in the paper's Table I.

    Edge list and link lengths (km) come from the FUSION optical-network
    simulator's data/raw/us_network.txt (github.com/SDNNetSim/FUSION). The
    file lists each link in both directions; one link (6-7) has two
    different lengths (900/1150 km) and the smaller is used. Node i in the
    file becomes switch s(i+1).

    Routing weight = km, so primaries are geographic shortest paths. The
    measured flows are the `n_flows` node pairs that are farthest apart
    (coast-to-coast in a continental backbone), standing in for the paper's
    "20 shortest paths from East- to West-coast routers".
    """
    lengths = {}
    with open(os.path.join(HERE, "usnet_fusion.txt")) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 3:
                continue
            a, b, km = int(parts[0]) + 1, int(parts[1]) + 1, float(parts[2])
            key = canon("s%d" % a, "s%d" % b)
            lengths[key] = min(km, lengths.get(key, km))
    links = [(a, b, km) for (a, b), km in sorted(lengths.items(),
             key=lambda kv: (_natkey(kv[0][0]), _natkey(kv[0][1])))]
    switches = ["s%d" % i for i in range(1, 25)]
    hosts = {"h%d" % i: "s%d" % i for i in range(1, 25)}
    topo = Topology(
        name="usnet",
        switches=switches,
        links=links,
        hosts=hosts,
        flows=[],
        link_km={(a, b): km for a, b, km in links},
        description="USNET 24 nodes / 43 links, random cut on a primary path",
    )
    assert len(switches) == 24 and len(links) == 43, (len(switches), len(links))

    # Farthest pairs by routed distance.
    from .routing import distances_from
    pairs = []
    for i, s in enumerate(switches):
        dist = distances_from(topo, s)
        for t in switches[i + 1:]:
            pairs.append((dist[t], s, t))
    pairs.sort(key=lambda p: (-p[0], _natkey(p[1]), _natkey(p[2])))
    topo.flows = [("h%s" % s[1:], "h%s" % t[1:]) for _, s, t in pairs[:n_flows]]
    return topo


TOPOLOGIES = {"triangle": triangle, "ring5": ring5, "usnet": usnet}


def get(name):
    try:
        return TOPOLOGIES[name]()
    except KeyError:
        raise SystemExit("unknown topology %r (have: %s)" % (name, ", ".join(TOPOLOGIES)))
