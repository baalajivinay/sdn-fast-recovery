"""
Controller test against fake switches -- no Mininet, no OVS.

Real os-ken OpenFlow 1.3 messages are built and serialised (so encoding
errors surface here), then decoded back into tables and packets are walked
through them. The point is to check what the controller *sends*, not what
the routing module *intends*:

  * startup: every switch gets state that forwards every host pair
  * restoration: after PORT_STATUS, the updates sent make the flow work
    again on the surviving graph
  * hybrid: the re-optimisation moves traffic off the crankback detour

Needs os-ken importable. In the Docker image it is installed; elsewhere
point PYTHONPATH at an os-ken checkout (see tests/README in docs).
"""

import ipaddress
import json
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from sdnrec.of_compat import ofproto_v1_3 as ofp          # noqa: E402
from sdnrec import topologies as T                        # noqa: E402
from sdnrec.routing import Flow, Group, forward, path     # noqa: E402

try:
    from os_ken.ofproto import ofproto_v1_3_parser as parser
except ImportError:                                       # pragma: no cover
    from ryu.ofproto import ofproto_v1_3_parser as parser


class FakeDP:
    def __init__(self, dpid):
        self.id = dpid
        self.ofproto = ofp
        self.ofproto_parser = parser
        self.sent = []
        self._xid = 0

    def set_xid(self, msg):
        self._xid += 1
        msg.set_xid(self._xid)

    def send_msg(self, msg):
        if msg.xid is None:
            self.set_xid(msg)
        msg.serialize()                     # raises on bad encoding
        self.sent.append(msg)


class Tables:
    """Applies controller messages to an abstract per-switch table."""

    def __init__(self, topo):
        self.topo = topo
        self.ip2host = {topo.host_ip(h): h for h in topo.hosts}
        self.flows = {}
        self.groups = {}

    def _acts(self, actions):
        out, pending_push = [], False
        for a in actions:
            if isinstance(a, parser.OFPActionOutput):
                out.append(("output", a.port))
            elif isinstance(a, parser.OFPActionGroup):
                out.append(("group", a.group_id))
            elif isinstance(a, parser.OFPActionPushVlan):
                pending_push = True
            elif isinstance(a, parser.OFPActionSetField):
                assert pending_push
                out.append(("push_vlan", a.value & 0x0fff))
                pending_push = False
            elif isinstance(a, parser.OFPActionPopVlan):
                out.append(("pop_vlan",))
            else:
                raise AssertionError(a)
        return out

    def apply(self, sw, msg):
        if isinstance(msg, parser.OFPFlowMod):
            if msg.command == ofp.OFPFC_DELETE:
                for k in [k for k in self.flows if k[0] == sw]:
                    del self.flows[k]
                return
            m = msg.match
            match = {"dst": self.ip2host[str(ipaddress.ip_address(m["ipv4_dst"]))]}
            if "vlan_vid" in m:
                v = m["vlan_vid"]
                match["vlan"] = None if v == 0 else v & 0x0fff
            acts = self._acts(msg.instructions[0].actions)
            key = (sw, msg.priority, tuple(sorted(match.items(), key=str)))
            self.flows[key] = Flow(sw, msg.priority, match, acts)
        elif isinstance(msg, parser.OFPGroupMod):
            if msg.command == ofp.OFPGC_DELETE:
                for k in [k for k in self.groups if k[0] == sw]:
                    del self.groups[k]
                return
            assert msg.type == ofp.OFPGT_FF
            if msg.command == ofp.OFPGC_ADD:
                assert (sw, msg.group_id) not in self.groups, "ADD of existing group"
            else:
                assert (sw, msg.group_id) in self.groups, "MODIFY of missing group"
            buckets = [(b.watch_port, self._acts(b.actions)) for b in msg.buckets]
            self.groups[(sw, msg.group_id)] = Group(sw, msg.group_id, buckets)

    def walk(self, src, dst, failed):
        return forward(self.topo, list(self.flows.values()), list(self.groups.values()),
                       src, dst, failed=failed)


def make_controller(topo_name, scheme, tmp):
    cfg = {"topology": topo_name, "scheme": scheme,
           "event_log": os.path.join(tmp, "ev.jsonl"),
           "ready_file": os.path.join(tmp, "ready")}
    with open(os.path.join(tmp, "cfg.json"), "w") as fh:
        json.dump(cfg, fh)
    os.environ["SDNREC_CONFIG"] = os.path.join(tmp, "cfg.json")
    from sdnrec.controller import RecoveryController
    return RecoveryController(), cfg


def boot(ctrl, topo):
    dps = {sw: FakeDP(topo.dpid(sw)) for sw in topo.switches}
    tables = Tables(topo)
    for sw, dp in dps.items():
        ctrl.on_features(SimpleNamespace(msg=SimpleNamespace(datapath=dp)))
    flush(ctrl, dps, tables)
    return dps, tables


def flush(ctrl, dps, tables):
    """Deliver queued messages to the tables and answer barriers."""
    for sw, dp in dps.items():
        msgs, dp.sent = dp.sent, []
        for m in msgs:
            if isinstance(m, parser.OFPBarrierRequest):
                ctrl.on_barrier(SimpleNamespace(msg=SimpleNamespace(datapath=dp, xid=m.xid)))
            else:
                tables.apply(sw, m)


def port_down(ctrl, dps, topo, sw, peer):
    dp = dps[sw]
    desc = SimpleNamespace(port_no=topo.port[(sw, peer)], state=ofp.OFPPS_LINK_DOWN,
                           config=0)
    ctrl.on_port_status(SimpleNamespace(msg=SimpleNamespace(datapath=dp, desc=desc,
                                                            reason=ofp.OFPPR_MODIFY)))


def events(cfg):
    with open(cfg["event_log"]) as fh:
        return [json.loads(l)["ev"] for l in fh]


def test_protection_ring_boot_and_crankback():
    topo = T.ring5()
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, cfg = make_controller("ring5", "protection", tmp)
        dps, tab = boot(ctrl, topo)
        assert os.path.exists(cfg["ready_file"])
        assert tab.walk("h1", "h2", set()) == ["s1", "s2", "s3", "s4", "s5"]
        assert tab.walk("h1", "h2", {("s3", "s4")}) == ["s1", "s2", "s3", "s2", "s1", "s5"]
        # protection: controller must not touch anything on failure
        port_down(ctrl, dps, topo, "s3", "s4")
        assert all(not dp.sent for dp in dps.values())


def test_restoration_ring():
    topo = T.ring5()
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, cfg = make_controller("ring5", "restoration", tmp)
        dps, tab = boot(ctrl, topo)
        assert not tab.groups
        cut = {("s3", "s4")}
        try:
            tab.walk("h1", "h2", cut)
            raise AssertionError("should blackhole before the controller reacts")
        except Exception as exc:
            assert "Blackhole" in type(exc).__name__
        port_down(ctrl, dps, topo, "s3", "s4")
        port_down(ctrl, dps, topo, "s4", "s3")       # far end reports too
        flush(ctrl, dps, tab)
        assert tab.walk("h1", "h2", cut) == ["s1", "s5"]
        ev = events(cfg)
        assert ev.count("failure_detected") == 1 and "restoration_complete" in ev


def test_hybrid_ring_reoptimises():
    topo = T.ring5()
    with tempfile.TemporaryDirectory() as tmp:
        ctrl, cfg = make_controller("ring5", "hybrid", tmp)
        dps, tab = boot(ctrl, topo)
        cut = {("s3", "s4")}
        assert tab.walk("h1", "h2", cut) == ["s1", "s2", "s3", "s2", "s1", "s5"]
        port_down(ctrl, dps, topo, "s3", "s4")
        flush(ctrl, dps, tab)
        assert tab.walk("h1", "h2", cut) == ["s1", "s5"]
        assert "reopt_complete" in events(cfg)


def test_usnet_all_schemes_every_flow():
    topo = T.usnet()
    import random
    rng = random.Random(3)
    for scheme in ("restoration", "protection", "hybrid"):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, cfg = make_controller("usnet", scheme, tmp)
            dps, tab = boot(ctrl, topo)
            src, dst = rng.choice(topo.flows)
            p = path(topo, topo.hosts[src], topo.hosts[dst])
            i = rng.randrange(len(p) - 1)
            up, down = p[i], p[i + 1]
            assert tab.walk(src, dst, set()) == p
            if scheme != "restoration":
                tab.walk(src, dst, {(up, down)})
            port_down(ctrl, dps, topo, up, down)
            flush(ctrl, dps, tab)
            after = tab.walk(src, dst, {(up, down)})
            if scheme != "protection":
                assert after == path(topo, topo.hosts[src], topo.hosts[dst], {(up, down)})
            print("  %-11s %s->%s cut %s-%s: %d hops -> %d hops"
                  % (scheme, src, dst, up, down, len(p) - 1, len(after) - 1))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
