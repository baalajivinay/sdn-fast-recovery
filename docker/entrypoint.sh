#!/bin/bash
# Start Open vSwitch inside the container, run the requested command, hand
# results back to the invoking user (everything here runs as root).
set -e

usage() {
    cat <<'EOF'
SDN fast failure recovery: van Adrichem et al. (EWSDN 2014) replication

Commands:
  check            is the openvswitch kernel module available?
  test             offline tests: routing + controller (no Mininet, seconds)
  smoke            one trial of every E1 config (~2 min). Run this first.
  quick            all experiments, few trials (~20 min)  -> results/quick/
  full             all experiments, full trials (~1 h)    -> results/
  suite [args]     run_suite.py directly, e.g. suite --exp e3 --trials 50
  analyze [dir]    recompute summary + figures (default results/)
  sim              model-based placeholder results -> results_sim/
  shell            bash inside the testbed
  help             this message

Reference numbers from the published run: /work/reference/summary.json

Run with:
  docker run --rm -it --privileged -v /lib/modules:/lib/modules:ro \
      -v "$PWD/results:/work/results" ghcr.io/baalajivinay/sdn-fast-recovery <command>
EOF
}

cmd="${1:-help}"
case "$cmd" in
    help|-h|--help) usage; exit 0 ;;
esac

modprobe openvswitch 2>/dev/null || true
if [ ! -d /sys/module/openvswitch ]; then
    echo "[entrypoint] the openvswitch kernel module is not loaded."
    echo "  Load it on the host (sudo modprobe openvswitch), or pass"
    echo "  -v /lib/modules:/lib/modules:ro so the container can load it."
    [ "$cmd" = test ] || [ "$cmd" = sim ] || [ "$cmd" = shell ] || exit 1
fi
[ "$cmd" = check ] && { echo "ok openvswitch kernel module loaded"; exit 0; }

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

shift || true
status=0
case "$cmd" in
    test)
        python3 tests/test_routing.py && python3 tests/test_controller.py || status=$? ;;
    smoke)
        python3 experiments/run_suite.py --exp e1 --trials 1 --results-dir results/smoke \
            && python3 experiments/analyze.py --results-dir results/smoke || status=$? ;;
    quick)
        python3 experiments/run_suite.py --exp all --quick --results-dir results/quick \
            && python3 experiments/analyze.py --results-dir results/quick || status=$? ;;
    full)
        python3 experiments/run_suite.py --exp all \
            && python3 experiments/analyze.py --results-dir results || status=$? ;;
    suite)   python3 experiments/run_suite.py "$@" || status=$? ;;
    analyze) python3 experiments/analyze.py --results-dir "${1:-results}" || status=$? ;;
    sim)
        python3 sim/simulate.py \
            && python3 experiments/analyze.py --results-dir results_sim || status=$? ;;
    shell)   /bin/bash || status=$? ;;
    *)       "$cmd" "$@" || status=$? ;;
esac

# never leave loopback delayed
tc qdisc del dev lo root 2>/dev/null || true

if [ -n "${HOST_UID:-}" ]; then
    for d in /work/results /work/results_sim; do
        [ -d "$d" ] && chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$d" 2>/dev/null || true
    done
fi
exit "$status"
