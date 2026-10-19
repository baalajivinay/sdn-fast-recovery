"""
Exhaustive checks of the forwarding logic -- runs anywhere, no Mininet.

    python3 -m pytest tests/ -q        (or: python3 tests/test_routing.py)

For every topology, every host pair and every single-link failure, a packet
is pushed through the protection tables and must reach its destination
without looping. This is the property crankback breaks when it is done
without tags, so it is worth checking for every case, not just the paper's.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sdnrec import topologies as T                      # noqa: E402
from sdnrec.routing import (forward, protection_state,   # noqa: E402
                            restoration_state, path, Blackhole, tree_to)


def _pairs(topo):
    hosts = sorted(topo.hosts)
    return [(a, b) for a, b in itertools.permutations(hosts, 2)]


def test_paper_primaries():
    tri = T.triangle()
    assert path(tri, "s1", "s3") == ["s1", "s2", "s3"]          # A-B-C
    ring = T.ring5()
    assert path(ring, "s1", "s5") == ["s1", "s2", "s3", "s4", "s5"]   # A-B-C-D-E


def test_triangle_backup_is_direct():
    tri = T.triangle()
    flows, groups = protection_state(tri)
    p = forward(tri, flows, groups, "h1", "h2", failed={("s1", "s2")})
    assert p == ["s1", "s3"]                                     # A-C


def test_ring_crankback_matches_paper():
    ring = T.ring5()
    flows, groups = protection_state(ring)
    p = forward(ring, flows, groups, "h1", "h2", failed={("s3", "s4")})
    # paper: A->B->C->B->A->E
    assert p == ["s1", "s2", "s3", "s2", "s1", "s5"], p


def test_usnet_size():
    us = T.usnet()
    assert len(us.switches) == 24 and len(us.links) == 43
    assert len(us.flows) == 20
    assert len(set(us.flows)) == 20


def _every_single_failure(topo):
    flows, groups = protection_state(topo)
    checked = 0
    for link in topo.switch_links():
        for src, dst in _pairs(topo):
            # only pairs that stay connected after the cut
            if path(topo, topo.hosts[src], topo.hosts[dst], {link}) is None:
                continue
            forward(topo, flows, groups, src, dst, failed={link})
            checked += 1
    return checked


def test_protection_survives_every_single_failure_triangle():
    assert _every_single_failure(T.triangle()) > 0


def test_protection_survives_every_single_failure_ring():
    assert _every_single_failure(T.ring5()) > 0


def test_protection_survives_every_single_failure_usnet():
    n = _every_single_failure(T.usnet())
    assert n == 43 * 24 * 23, n          # USNET is 2-edge-connected


def test_restoration_blackholes_until_controller_acts():
    ring = T.ring5()
    flows, groups = restoration_state(ring)
    try:
        forward(ring, flows, groups, "h1", "h2", failed={("s3", "s4")})
        raise AssertionError("old state should not survive the cut")
    except Blackhole:
        pass
    flows2, _ = restoration_state(ring, failed={("s3", "s4")})
    assert forward(ring, flows2, [], "h1", "h2", failed={("s3", "s4")}) == ["s1", "s5"]


def test_hybrid_reoptimised_state_is_protected_again():
    us = T.usnet()
    rng = __import__("random").Random(7)
    for _ in range(25):
        first = rng.choice(us.switch_links())
        flows, groups = protection_state(us, failed={first})
        second = rng.choice([l for l in us.switch_links() if l != first])
        for src, dst in rng.sample(_pairs(us), 40):
            if path(us, us.hosts[src], us.hosts[dst], {first, second}) is None:
                continue
            forward(us, flows, groups, src, dst, failed={first, second})


def test_tags_fit_vlan_space():
    us = T.usnet()
    assert max(us.tag(l) for l in us.switch_links()) <= 4094


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
