"""
Mininet/OVS plumbing for the experiments: build a topology, configure BFD,
run the controller, emulate controller distance, inject and repair failures.

Runs as root inside the container (see run.sh).

Two failure types
-----------------
carrier  `ip link set <far-end interface> down`. The near-end veth loses
         carrier immediately, so loss-of-signal sees it at once. This is
         the most favourable case for LoS and is NOT what the paper
         measured: on their physical Ethernet, LoS took ~4.7 s in OVS.
silent   every frame on the link is dropped in both directions (tc ingress
         drop on both veth ends, applied in one `tc -batch` so the two
         directions fail within microseconds of each other) while carrier
         stays up. Only something that probes the link -- BFD -- can notice.
         This is the emulation of the paper's failure as far as the
         recovering switch is concerned (carrier gives no signal), and it
         is the realistic case for a fibre cut behind a transceiver or
         amplifier, a line card fault, or a grey failure.
"""

import json
import os
import signal
import subprocess
import tempfile
import time

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import Link, TCLink
from mininet.clean import cleanup as mn_cleanup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTRL_PORT = 6653


def sh(cmd, check=False):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
    if check and p.returncode:
        raise RuntimeError("command failed (%d): %s\n%s" % (p.returncode, cmd, p.stdout))
    return p.stdout


def intf(topo, a, b):
    """Name of a's interface toward b (Mininet names ports <node>-eth<n>)."""
    return "%s-eth%d" % (a, topo.port[(a, b)])


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def build(topo, link_delay_ms=0, geo_delay=False):
    """
    Build and start the Mininet network for `topo`.

    link_delay_ms  propagation delay per unit of routing weight
    geo_delay      USNET only: delay = link length / (2e5 km/s)
    """
    mn_cleanup()
    shaped = bool(link_delay_ms) or geo_delay
    net = Mininet(switch=OVSSwitch, link=TCLink if shaped else Link, controller=None,
                  autoSetMacs=False, autoStaticArp=False, build=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=CTRL_PORT)
    for s in topo.switches:
        net.addSwitch(s, dpid="%016x" % topo.dpid(s), protocols="OpenFlow13",
                      failMode="secure")
    for h, s in sorted(topo.hosts.items()):
        net.addHost(h, ip=topo.host_ip(h) + "/8", mac=topo.host_mac(h))
        net.addLink(h, s, port1=0, port2=topo.host_port[h])
    for a, b, _w in topo.links:
        kw = {}
        if geo_delay and topo.link_km:
            kw["delay"] = "%.3fms" % (topo.link_km[(a, b)] / 200.0)
        elif link_delay_ms:
            # delay follows the routing weight, so "expensive" links are also
            # long ones (ring E-A: weight 10 -> 10 ms) and latency tells the
            # same story as the path choice
            kw["delay"] = "%gms" % (link_delay_ms * _w)
        net.addLink(a, b, port1=topo.port[(a, b)], port2=topo.port[(b, a)], **kw)
    net.build()
    net.start()
    net.staticArp()
    for h in net.hosts:
        h.cmd("sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")
    # Switch ports also emit IPv6 router solicitations etc.; silence them.
    sh("sysctl -qw net.ipv6.conf.all.disable_ipv6=1")
    # Reconnect quickly when the controller is restarted between trials
    # (OVS's default backoff grows to 8 s).
    uuids = sh("ovs-vsctl --bare --columns=_uuid list controller").split()
    if uuids:
        sh("ovs-vsctl " + " -- ".join("set controller %s max_backoff=1000 "
                                       "inactivity_probe=30000" % u for u in uuids))
    verify_ports(topo)
    return net


def verify_ports(topo):
    """Fail loudly if OVS numbered a port differently from the topology."""
    for (a, b), p in topo.port.items():
        got = sh("ovs-vsctl get interface %s ofport" % intf(topo, a, b)).strip()
        if got != str(p):
            raise RuntimeError("port mismatch on %s: OVS says %s, topology says %d"
                               % (intf(topo, a, b), got, p))


# ---------------------------------------------------------------------------
# BFD
# ---------------------------------------------------------------------------

def switch_intfs(topo):
    out = []
    for a, b in topo.switch_links():
        out += [intf(topo, a, b), intf(topo, b, a)]
    return out


def set_bfd(topo, interval_ms, mult=3):
    """interval_ms=None disables BFD (pure loss-of-signal)."""
    cmds = []
    for i in switch_intfs(topo):
        if interval_ms:
            cmds.append("set interface %s bfd:enable=true bfd:min_tx=%d bfd:min_rx=%d "
                        "bfd:mult=%d" % (i, interval_ms, interval_ms, mult))
        else:
            cmds.append("clear interface %s bfd" % i)
    sh("ovs-vsctl " + " -- ".join(cmds), check=True)


def bfd_state(topo):
    raw = sh("ovs-vsctl --format=json --columns=name,bfd_status list interface")
    data = json.loads(raw)
    state = {}
    for name, status in data["data"]:
        pairs = dict(status[1]) if isinstance(status, list) and status[0] == "map" else {}
        state[name] = pairs.get("forwarding")
    return {i: state.get(i) for i in switch_intfs(topo)}


def wait_bfd_up(topo, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = bfd_state(topo)
        if all(v == "true" for v in st.values()):
            return True
        time.sleep(0.2)
    down = [k for k, v in bfd_state(topo).items() if v != "true"]
    raise RuntimeError("BFD not forwarding on %s" % down)


# ---------------------------------------------------------------------------
# Controller distance (netem on loopback; see README)
# ---------------------------------------------------------------------------

def set_control_delay(ms):
    if ms:
        sh("tc qdisc replace dev lo root netem delay %dms" % ms, check=True)


def clear_control_delay():
    sh("tc qdisc del dev lo root 2>/dev/null")


# ---------------------------------------------------------------------------
# Controller process
# ---------------------------------------------------------------------------

class Controller:
    def __init__(self, topo, scheme, trial_dir):
        self.trial_dir = trial_dir
        self.ready_file = os.path.join(trial_dir, ".ready")
        self.cfg_path = os.path.join(trial_dir, "controller_cfg.json")
        cfg = {"topology": topo.name, "scheme": scheme,
               "event_log": os.path.join(trial_dir, "controller_events.jsonl"),
               "ready_file": self.ready_file}
        with open(self.cfg_path, "w") as fh:
            json.dump(cfg, fh)
        for f in (self.ready_file, cfg["event_log"]):
            if os.path.exists(f):
                os.remove(f)
        env = dict(os.environ, SDNREC_CONFIG=self.cfg_path,
                   PYTHONPATH=ROOT + ":" + os.environ.get("PYTHONPATH", ""))
        self.log = open(os.path.join(trial_dir, "controller.log"), "w")
        self.proc = subprocess.Popen(
            ["python3", os.path.join(ROOT, "sdnrec", "run_controller.py"),
             "--ofp-tcp-listen-port", str(CTRL_PORT),
             os.path.join(ROOT, "sdnrec", "controller.py")],
            stdout=self.log, stderr=subprocess.STDOUT, env=env, cwd=ROOT,
            preexec_fn=os.setsid)

    def wait_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.ready_file):
                return True
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        self.log.flush()
        with open(self.log.name) as fh:
            tail = fh.read()[-2000:]
        raise RuntimeError("controller not ready (exit=%s):\n%s" % (self.proc.poll(), tail))

    def stop(self):
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        self.log.close()


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

def _tc_batch(lines):
    with tempfile.NamedTemporaryFile("w", suffix=".tc", delete=False) as fh:
        fh.write("\n".join(lines) + "\n")
        path = fh.name
    try:
        return sh("tc -force -batch %s" % path)
    finally:
        os.remove(path)


def prepare_silent(topo, link):
    """Add ingress qdiscs ahead of time so the failure itself is one step."""
    a, b = link
    for i in (intf(topo, a, b), intf(topo, b, a)):
        sh("tc qdisc del dev %s ingress 2>/dev/null" % i)
        sh("tc qdisc add dev %s ingress" % i, check=True)


def inject(topo, kind, upstream, downstream):
    """
    Fail link upstream-downstream. Returns (t_before, t_after).

    carrier: the far end (downstream) interface goes down, as in the paper
             where `ifdown` was issued on the switch opposite the one that
             recovers.
    silent : drop everything on both ends at once.
    """
    if kind == "carrier":
        cmd = "ip link set %s down" % intf(topo, downstream, upstream)
        t0 = time.time()
        sh(cmd, check=True)
        return t0, time.time()
    if kind == "silent":
        lines = ["filter add dev %s parent ffff: protocol all prio 1 matchall action drop" % i
                 for i in (intf(topo, downstream, upstream), intf(topo, upstream, downstream))]
        t0 = time.time()
        out = _tc_batch(lines)
        t1 = time.time()
        if "rror" in out or "nknown" in out:
            # Kernel without cls_matchall: u32 catch-all instead.
            lines = [l.replace("matchall", "u32 match u32 0 0") for l in lines]
            t0 = time.time()
            out = _tc_batch(lines)
            t1 = time.time()
            if "rror" in out:
                raise RuntimeError("could not install drop filters:\n" + out)
        return t0, t1
    raise ValueError(kind)


def repair(topo, kind, upstream, downstream):
    if kind == "carrier":
        sh("ip link set %s up" % intf(topo, downstream, upstream), check=True)
    else:
        for i in (intf(topo, downstream, upstream), intf(topo, upstream, downstream)):
            sh("tc qdisc del dev %s ingress 2>/dev/null" % i)
    # wait for carrier on both ends
    deadline = time.time() + 10
    while time.time() < deadline:
        ok = all("state UP" in sh("ip link show %s" % i) or "LOWER_UP" in sh("ip link show %s" % i)
                 for i in (intf(topo, downstream, upstream), intf(topo, upstream, downstream)))
        if ok:
            return
        time.sleep(0.1)


def dump_state(topo):
    """Flow and group entries actually present in OVS, per switch."""
    out = {}
    for s in topo.switches:
        flows = [l for l in sh("ovs-ofctl -O OpenFlow13 dump-flows %s" % s).splitlines()
                 if "cookie=" in l]
        groups = [l for l in sh("ovs-ofctl -O OpenFlow13 dump-groups %s" % s).splitlines()
                  if "group_id=" in l]
        out[s] = {"flows": len(flows), "groups": len(groups)}
    return out
