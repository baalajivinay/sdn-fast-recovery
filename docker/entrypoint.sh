#!/bin/bash
# Start Open vSwitch inside the container, run the command, hand results
# back to the invoking user (everything here runs as root).
set -e

modprobe openvswitch 2>/dev/null || \
    echo "[entrypoint] note: could not modprobe openvswitch; using the host's loaded module"

mkdir -p /var/run/openvswitch /var/log/openvswitch /etc/openvswitch
[ -f /etc/openvswitch/conf.db ] || \
    ovsdb-tool create /etc/openvswitch/conf.db /usr/share/openvswitch/vswitch.ovsschema
ovsdb-server /etc/openvswitch/conf.db \
    --remote=punix:/var/run/openvswitch/db.sock \
    --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
    --pidfile --detach --log-file >/dev/null 2>&1
ovs-vsctl --no-wait init
ovs-vswitchd --pidfile --detach --log-file >/dev/null 2>&1
echo "[entrypoint] Open vSwitch $(ovs-vsctl --version | head -1 | awk '{print $NF}') ready"

mn -c >/dev/null 2>&1 || true
tc qdisc del dev lo root 2>/dev/null || true   # leftover controller-delay emulation

status=0
"$@" || status=$?

# never leave the host's loopback delayed
tc qdisc del dev lo root 2>/dev/null || true

if [ -n "${HOST_UID:-}" ]; then
    for d in /work/results /work/results_sim; do
        [ -d "$d" ] && chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$d" 2>/dev/null || true
    done
fi
exit "$status"
