"""
Forwarding-state computation, independent of any controller framework.

The controller turns the abstract state built here into OpenFlow messages;
the simulator counts it; the tests push packets through it. Keeping it free
of os-ken means the routing logic -- the part most likely to be subtly wrong
-- can be checked exhaustively without Mininet.

State model
-----------
    Flow(sw, priority, match, actions)
        match keys:  "dst"  -> destination host name (matched as ipv4_dst)
                     "vlan" -> None  : packet must be untagged
                               int   : packet must carry this VLAN id
                               (key absent: any)
        actions:     ("output", port) | ("group", gid) | ("push_vlan", vid)
                     | ("pop_vlan",)
    Group(sw, gid, buckets)            fast-failover; bucket = (watch_port, actions)

Schemes
-------
restoration   only the working tree; the controller rewrites it after a
              failure is reported (reactive).
protection    van Adrichem et al. 2014: every switch on every destination
              tree gets a fast-failover group. Bucket 1 is the shortest-path
              next hop; bucket 2 is the first hop of the shortest path that
              avoids that link. Bucket 2 tags the packet with the failed
              link's id; tagged packets then follow precomputed per-failure
              rules all the way to the egress, which pops the tag.
              When the detour's first hop points back upstream, this is
              crankback (paper Fig. 3b: C->B->A->E). The tag is what keeps
              it loop-free: an upstream switch sees a tagged packet and
              follows the detour instead of its own primary.
"""

import heapq
from collections import namedtuple

from .topologies import canon, _natkey

Flow = namedtuple("Flow", "sw priority match actions")
Group = namedtuple("Group", "sw gid buckets")

PRIO_PRIMARY = 100
PRIO_DETOUR = 200


# ---------------------------------------------------------------------------
# Shortest paths
# ---------------------------------------------------------------------------

def _dijkstra(topo, root, removed=frozenset()):
    """Distances and next hops *toward* root, deterministic tie-breaking."""
    removed = {canon(*l) for l in removed}
    dist = {root: (0.0, 0)}
    nexthop = {}
    heap = [(0.0, 0, _natkey(root), root)]
    done = set()
    while heap:
        d, hops, _, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        for v, w in topo.neighbors(u):
            if canon(u, v) in removed or v in done:
                continue
            cand = (d + w, hops + 1)
            if v not in dist or cand < dist[v]:
                dist[v] = cand
                nexthop[v] = u          # v forwards to u to reach root
                heapq.heappush(heap, (cand[0], cand[1], _natkey(v), v))
    return dist, nexthop


def distances_from(topo, src, removed=frozenset()):
    dist, _ = _dijkstra(topo, src, removed)
    return {n: d[0] for n, d in dist.items()}


def tree_to(topo, dst_sw, removed=frozenset()):
    """{switch: next hop toward dst_sw} over the graph minus `removed`."""
    return _dijkstra(topo, dst_sw, removed)[1]


def path(topo, src_sw, dst_sw, removed=frozenset()):
    nh = tree_to(topo, dst_sw, removed)
    p = [src_sw]
    while p[-1] != dst_sw:
        if p[-1] not in nh:
            return None
        p.append(nh[p[-1]])
    return p


def path_cost(topo, p):
    return sum(topo.weight(a, b) for a, b in zip(p, p[1:]))


# ---------------------------------------------------------------------------
# Forwarding state per scheme
# ---------------------------------------------------------------------------

def restoration_state(topo, failed=frozenset()):
    flows = []
    for h, t in sorted(topo.hosts.items(), key=lambda kv: _natkey(kv[0])):
        nh = tree_to(topo, t, failed)
        flows.append(Flow(t, PRIO_PRIMARY, {"dst": h}, [("output", topo.host_port[h])]))
        for u, v in sorted(nh.items(), key=lambda kv: _natkey(kv[0])):
            flows.append(Flow(u, PRIO_PRIMARY, {"dst": h},
                              [("output", topo.port[(u, v)])]))
    return flows, []


def gid_of(host):
    return int(host[1:])


def protection_state(topo, failed=frozenset()):
    """
    Fast-failover link protection with tagged crankback detours.

    `failed` lets the hybrid scheme recompute full protection for the
    post-failure topology (the controller's re-optimisation step).
    """
    failed = frozenset(canon(*l) for l in failed)
    flows = {}
    groups = []

    def add(flow):
        key = (flow.sw, flow.priority, tuple(sorted(flow.match.items(), key=str)))
        prev = flows.get(key)
        if prev is not None and prev.actions != flow.actions:
            raise AssertionError("conflicting rules for %r: %r vs %r"
                                 % (key, prev.actions, flow.actions))
        flows[key] = flow

    for h, t in sorted(topo.hosts.items(), key=lambda kv: _natkey(kv[0])):
        hp = topo.host_port[h]
        add(Flow(t, PRIO_PRIMARY, {"dst": h, "vlan": None}, [("output", hp)]))
        nh0 = tree_to(topo, t, failed)
        for u in sorted(nh0, key=_natkey):
            v = nh0[u]
            link = canon(u, v)
            tag = topo.tag(link)
            buckets = [(topo.port[(u, v)], [("output", topo.port[(u, v)])])]
            nhL = tree_to(topo, t, failed | {link})
            if u in nhL:
                alt = nhL[u]
                buckets.append((topo.port[(u, alt)],
                                [("push_vlan", tag), ("output", topo.port[(u, alt)])]))
                x = alt
                while x != t:
                    add(Flow(x, PRIO_DETOUR, {"dst": h, "vlan": tag},
                             [("output", topo.port[(x, nhL[x])])]))
                    x = nhL[x]
                add(Flow(t, PRIO_DETOUR, {"dst": h, "vlan": tag},
                         [("pop_vlan",), ("output", hp)]))
            groups.append(Group(u, gid_of(h), buckets))
            add(Flow(u, PRIO_PRIMARY, {"dst": h, "vlan": None}, [("group", gid_of(h))]))
    return list(flows.values()), groups


def state_for(scheme, topo, failed=frozenset()):
    if scheme == "restoration":
        return restoration_state(topo, failed)
    if scheme in ("protection", "hybrid"):
        return protection_state(topo, failed)
    raise ValueError(scheme)


def state_size(flows, groups):
    """Entries a switch has to hold: flow entries + group entries (buckets)."""
    return {
        "flow_entries": len(flows),
        "group_entries": len(groups),
        "buckets": sum(len(g.buckets) for g in groups),
        "per_switch_max": max(
            [sum(1 for f in flows if f.sw == s) + sum(1 for g in groups if g.sw == s)
             for s in {f.sw for f in flows} | {g.sw for g in groups}] or [0]),
    }


# ---------------------------------------------------------------------------
# Data-plane walk: what a packet actually does with this state
# ---------------------------------------------------------------------------

class Blackhole(Exception):
    pass


class Loop(Exception):
    pass


def forward(topo, flows, groups, src_host, dst_host, failed=frozenset(), max_hops=200):
    """
    Push one packet through the abstract tables. Returns the switch path.

    Port liveness for fast-failover = the link is not in `failed`. Raises
    Blackhole if the packet is dropped or sent into a failed link, Loop if
    it revisits a (switch, tag) state.
    """
    failed = {canon(*l) for l in failed}
    by_sw = {}
    for f in flows:
        by_sw.setdefault(f.sw, []).append(f)
    for lst in by_sw.values():
        lst.sort(key=lambda f: -f.priority)
    gtab = {(g.sw, g.gid): g for g in groups}
    port_peer = {(a, p): b for (a, b), p in topo.port.items()}

    def live(sw, port):
        peer = port_peer.get((sw, port))
        return peer is None or canon(sw, peer) not in failed

    sw = topo.hosts[src_host]
    vlan = None
    trail, seen = [sw], set()
    for _ in range(max_hops):
        if (sw, vlan) in seen:
            raise Loop(trail)
        seen.add((sw, vlan))
        rule = None
        for f in by_sw.get(sw, []):
            if f.match.get("dst") != dst_host:
                continue
            if "vlan" in f.match and f.match["vlan"] != vlan:
                continue
            rule = f
            break
        if rule is None:
            raise Blackhole(("no rule", sw, vlan, trail))
        actions = list(rule.actions)
        out = None
        while actions:
            act = actions.pop(0)
            if act[0] == "group":
                g = gtab[(sw, act[1])]
                chosen = next((b for b in g.buckets if live(sw, b[0])), None)
                if chosen is None:
                    raise Blackhole(("no live bucket", sw, trail))
                actions = list(chosen[1]) + actions
            elif act[0] == "push_vlan":
                vlan = act[1]
            elif act[0] == "pop_vlan":
                vlan = None
            elif act[0] == "output":
                out = act[1]
        if out is None:
            raise Blackhole(("no output", sw, trail))
        if (sw, out) not in port_peer:          # host port
            if out != topo.host_port.get(dst_host) or topo.hosts[dst_host] != sw:
                raise Blackhole(("wrong host port", sw, out, trail))
            if vlan is not None:
                raise Blackhole(("delivered tagged", sw, trail))
            return trail
        if not live(sw, out):
            raise Blackhole(("sent into failed link", sw, out, trail))
        sw = port_peer[(sw, out)]
        trail.append(sw)
    raise Loop(trail)
