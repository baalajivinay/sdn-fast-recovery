"""
One os-ken application, three recovery schemes.

    SDNREC_CONFIG=/path/cfg.json python3 sdnrec/run_controller.py sdnrec/controller.py

cfg.json:
    {"topology": "ring5", "scheme": "restoration|protection|hybrid",
     "event_log": "/.../controller_events.jsonl", "ready_file": "/.../.ready"}

restoration  installs only working paths. On PORT_STATUS it recomputes
             shortest paths over the surviving graph and rewrites the
             entries that changed. Every step is on the critical path.
protection   van Adrichem et al. 2014: fast-failover groups with tagged
             crankback detours, installed up front. The controller does
             nothing when a link fails -- the switch already knows what to do.
hybrid       protection, plus the paper's last step: once the failure is
             reported, the controller recomputes full protection for the
             post-failure topology, moving traffic off the crankback detour
             onto the new shortest path (and protecting it again).

How the switch learns about the failure is not decided here. Fast-failover
watches port liveness, which in Open vSwitch is carrier AND (if enabled)
BFD; OVS also clears OFPPS_LIVE and sends PORT_STATUS when BFD declares a
link down. So whether a scheme runs on loss-of-signal or on BFD is purely a
property of how the experiment driver configured the interfaces.

The topology is given, not discovered: LLDP discovery re-scans on a
seconds-scale timer, and a restoration controller relying on it would be
measuring that timer instead of restoration.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdnrec.of_compat import (ControllerApp, ofp_event, CONFIG_DISPATCHER,  # noqa: E402
                              MAIN_DISPATCHER, set_ev_cls, ofproto_v1_3)
from sdnrec import topologies                                                # noqa: E402
from sdnrec.routing import state_for                                         # noqa: E402

OFPVID_PRESENT = 0x1000
OFPVID_NONE = 0x0000


def flow_key(f):
    return (f.sw, f.priority, tuple(sorted(f.match.items(), key=str)))


class RecoveryController(ControllerApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cfg_path = os.environ.get("SDNREC_CONFIG")
        if not cfg_path:
            raise SystemExit("SDNREC_CONFIG not set")
        with open(cfg_path) as fh:
            self.cfg = json.load(fh)
        self.topo = topologies.get(self.cfg["topology"])
        self.scheme = self.cfg["scheme"]
        if self.scheme not in ("restoration", "protection", "hybrid"):
            raise SystemExit("unknown scheme %r" % self.scheme)
        self.failed = set()
        self.dps = {}
        self.pending = {}            # (dpid, xid) -> phase name
        self.phase_waiting = {}      # phase -> set of dpids still to answer
        self.armed = False
        self._evlog = open(self.cfg["event_log"], "a", buffering=1)

        self.flows, self.groups = state_for(self.scheme, self.topo)
        self.flow_by_key = {flow_key(f): f for f in self.flows}
        self.group_by_key = {(g.sw, g.gid): g for g in self.groups}
        self.event("start", scheme=self.scheme, topology=self.topo.name,
                   flows=len(self.flows), groups=len(self.groups))

    # ------------------------------------------------------------------ log
    def event(self, name, **kw):
        kw.update(t=time.time(), ev=name)
        self._evlog.write(json.dumps(kw) + "\n")

    # --------------------------------------------------------- translation
    def _match(self, dp, m):
        p = dp.ofproto_parser
        kw = {"eth_type": 0x0800, "ipv4_dst": self.topo.host_ip(m["dst"])}
        if "vlan" in m:
            kw["vlan_vid"] = OFPVID_NONE if m["vlan"] is None else (OFPVID_PRESENT | m["vlan"])
        return p.OFPMatch(**kw)

    def _actions(self, dp, acts):
        p = dp.ofproto_parser
        out = []
        for a in acts:
            if a[0] == "output":
                out.append(p.OFPActionOutput(a[1]))
            elif a[0] == "group":
                out.append(p.OFPActionGroup(a[1]))
            elif a[0] == "push_vlan":
                out.append(p.OFPActionPushVlan(0x8100))
                out.append(p.OFPActionSetField(vlan_vid=OFPVID_PRESENT | a[1]))
            elif a[0] == "pop_vlan":
                out.append(p.OFPActionPopVlan())
            else:
                raise ValueError(a)
        return out

    def flow_mod(self, dp, f):
        p, o = dp.ofproto_parser, dp.ofproto
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, self._actions(dp, f.actions))]
        return p.OFPFlowMod(datapath=dp, priority=f.priority, match=self._match(dp, f.match),
                            instructions=inst, command=o.OFPFC_ADD)

    def group_mod(self, dp, g, command):
        p, o = dp.ofproto_parser, dp.ofproto
        buckets = [p.OFPBucket(watch_port=wp, watch_group=o.OFPG_ANY,
                               actions=self._actions(dp, acts))
                   for wp, acts in g.buckets]
        return p.OFPGroupMod(dp, command=command, type_=o.OFPGT_FF,
                             group_id=g.gid, buckets=buckets)

    def barrier(self, dp, phase):
        msg = dp.ofproto_parser.OFPBarrierRequest(dp)
        dp.send_msg(msg)
        self.pending[(dp.id, msg.xid)] = phase
        self.phase_waiting.setdefault(phase, set()).add(dp.id)

    # ------------------------------------------------------------ handlers
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath
        p, o = dp.ofproto_parser, dp.ofproto
        sw = self.topo.sw_of_dpid(dp.id)
        if sw not in self.topo.switches:
            self.event("unknown_switch", dpid=dp.id)
            return
        self.dps[sw] = dp
        # Start from a clean switch: a previous trial's controller may have
        # rewritten its tables. OVS processes one connection's messages in
        # order, so groups exist before the flows that point at them.
        dp.send_msg(p.OFPFlowMod(datapath=dp, command=o.OFPFC_DELETE, table_id=o.OFPTT_ALL,
                                 out_port=o.OFPP_ANY, out_group=o.OFPG_ANY,
                                 match=p.OFPMatch()))
        dp.send_msg(p.OFPGroupMod(dp, command=o.OFPGC_DELETE, group_id=o.OFPG_ALL))
        n_g = n_f = 0
        for g in self.groups:
            if g.sw == sw:
                dp.send_msg(self.group_mod(dp, g, o.OFPGC_ADD))
                n_g += 1
        for f in self.flows:
            if f.sw == sw:
                dp.send_msg(self.flow_mod(dp, f))
                n_f += 1
        self.event("switch_programmed_sent", sw=sw, flows=n_f, groups=n_g)
        self.barrier(dp, "install")

    @set_ev_cls(ofp_event.EventOFPBarrierReply, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def on_barrier(self, ev):
        dp = ev.msg.datapath
        phase = self.pending.pop((dp.id, ev.msg.xid), None)
        if phase is None:
            return
        waiting = self.phase_waiting.get(phase, set())
        waiting.discard(dp.id)
        if waiting:
            return
        if phase == "install":
            if len(self.dps) == len(self.topo.switches) and not self.armed:
                self.armed = True
                self.event("ready")
                with open(self.cfg["ready_file"], "w") as fh:
                    json.dump({"flows": len(self.flows), "groups": len(self.groups),
                               "t": time.time()}, fh)
        else:
            self.event(phase + "_complete")

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def on_error(self, ev):
        m = ev.msg
        self.event("of_error", dpid=m.datapath.id, type=m.type, code=m.code,
                   data=m.data.hex()[:120] if m.data else "")

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def on_port_status(self, ev):
        msg = ev.msg
        dp = msg.datapath
        o = dp.ofproto
        d = msg.desc
        sw = self.topo.sw_of_dpid(dp.id)
        down = bool(d.state & o.OFPPS_LINK_DOWN or d.config & o.OFPPC_PORT_DOWN
                    or not d.state & o.OFPPS_LIVE)
        link = self.topo.link_of_port(sw, d.port_no)
        self.event("port_status", sw=sw, port=d.port_no, down=down, state=d.state,
                   config=d.config, reason=msg.reason,
                   link=list(link) if link else None, armed=self.armed)
        if not (down and link and self.armed) or link in self.failed:
            return
        self.failed.add(link)
        self.event("failure_detected", link=list(link), scheme=self.scheme)
        if self.scheme == "restoration":
            self.restore()
        elif self.scheme == "hybrid":
            self.reoptimise()

    # ------------------------------------------------------------ recovery
    def restore(self):
        t0 = time.time()
        new_flows, _ = state_for("restoration", self.topo, frozenset(self.failed))
        changed = [f for f in new_flows
                   if self.flow_by_key.get(flow_key(f), None) is None
                   or self.flow_by_key[flow_key(f)].actions != f.actions]
        touched = set()
        for f in changed:
            dp = self.dps.get(f.sw)
            if dp is None:
                continue
            dp.send_msg(self.flow_mod(dp, f))
            self.flow_by_key[flow_key(f)] = f
            touched.add(f.sw)
        self.event("flows_sent", phase="restoration", n=len(changed),
                   switches=sorted(touched), compute_ms=(time.time() - t0) * 1e3)
        for sw in sorted(touched):
            self.barrier(self.dps[sw], "restoration")

    def reoptimise(self):
        t0 = time.time()
        new_flows, new_groups = state_for("hybrid", self.topo, frozenset(self.failed))
        per_sw = {}
        for f in new_flows:
            old = self.flow_by_key.get(flow_key(f))
            if old is None or old.actions != f.actions:
                per_sw.setdefault(f.sw, {"detour": [], "primary": [], "groups": []})
                kind = "detour" if f.match.get("vlan") is not None else "primary"
                per_sw[f.sw][kind].append(f)
                self.flow_by_key[flow_key(f)] = f
        for g in new_groups:
            old = self.group_by_key.get((g.sw, g.gid))
            if old is None or old.buckets != g.buckets:
                per_sw.setdefault(g.sw, {"detour": [], "primary": [], "groups": []})
                per_sw[g.sw]["groups"].append((g, old is None))
                self.group_by_key[(g.sw, g.gid)] = g
        n = 0
        for sw, todo in sorted(per_sw.items()):
            dp = self.dps.get(sw)
            if dp is None:
                continue
            o = dp.ofproto
            # Make-before-break inside each switch: detour rules first, then
            # the groups that start using them, then any new primaries.
            for f in todo["detour"]:
                dp.send_msg(self.flow_mod(dp, f))
            for g, is_new in todo["groups"]:
                dp.send_msg(self.group_mod(dp, g, o.OFPGC_ADD if is_new else o.OFPGC_MODIFY))
            for f in todo["primary"]:
                dp.send_msg(self.flow_mod(dp, f))
            n += len(todo["detour"]) + len(todo["groups"]) + len(todo["primary"])
        self.event("flows_sent", phase="reopt", n=n, switches=sorted(per_sw),
                   compute_ms=(time.time() - t0) * 1e3)
        for sw in sorted(per_sw):
            if sw in self.dps:
                self.barrier(self.dps[sw], "reopt")
