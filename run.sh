#!/usr/bin/env bash
#
# SDN fast failure recovery -- van Adrichem et al. (EWSDN 2014) replication.
#
#   ./run.sh check            preflight: Docker, openvswitch module, conflicts
#   ./run.sh build            build the container image (once)
#   ./run.sh test             offline tests: routing + controller (no Mininet)
#   ./run.sh smoke            one trial of every E1 config (~2 min) -- run this first
#   ./run.sh quick            all experiments, few trials (~20 min) + analysis
#   ./run.sh full             all experiments, full trials (~1 h) + analysis
#   ./run.sh suite [args]     run_suite.py directly, e.g. --exp e3 --trials 50
#   ./run.sh analyze [dir]    recompute summary + figures (default results/)
#   ./run.sh sim              model-based draft results into results_sim/
#   ./run.sh shell            shell inside the testbed container
#
# This mounts the repo over /work so local edits take effect without a rebuild.
# To replicate without cloning, use the published image (see README).
#
set -euo pipefail

IMAGE="sdnrec-vanadrichem:latest"
OLD_IMAGE="sdn-recovery:latest"          # image from the previous project
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --privileged: Mininet creates namespaces/veths, OVS needs the kernel module.
# --network host: the campus DNS servers (172.17.18.x) collide with Docker's
#   default bridge subnet, so bridged containers resolve nothing (found in the
#   previous project). Host networking also means the controller-delay netem
#   sits on the host's loopback while an experiment runs; the entrypoint
#   removes it on exit.
RUN=(docker run --rm -i --privileged --network host
     -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)"
     -v "${REPO}:/work" -w /work "${IMAGE}")
[ -t 0 ] && RUN=(docker run --rm -it --privileged --network host
     -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)"
     -v "${REPO}:/work" -w /work "${IMAGE}")

check() {
    local ok=1
    command -v docker >/dev/null || { echo "x docker not found"; ok=0; }
    if modinfo openvswitch >/dev/null 2>&1; then echo "ok openvswitch kernel module available"
    else echo "x openvswitch kernel module missing (sudo apt install openvswitch-switch)"; ok=0; fi
    if pgrep -x ovs-vswitchd >/dev/null; then
        echo "! ovs-vswitchd is running on the host. Two vswitchd instances share one"
        echo "  kernel datapath and can delete each other's ports. Stop it for the run:"
        echo "     sudo systemctl stop openvswitch-switch      (start it again afterwards)"
    else
        echo "ok no host ovs-vswitchd running"
    fi
    if tc qdisc show dev lo 2>/dev/null | grep -q netem; then
        echo "! loopback still has a netem delay from an interrupted run:"
        echo "     sudo tc qdisc del dev lo root"
    fi
    docker image inspect "${IMAGE}" >/dev/null 2>&1 && echo "ok image ${IMAGE} built" \
        || echo "- image not built yet: ./run.sh build"
    [ $ok = 1 ]
}

cmd="${1:-help}"; shift || true
case "$cmd" in
    check) check ;;
    build)
        base_arg=()
        if docker image inspect "${OLD_IMAGE}" >/dev/null 2>&1; then
            echo "reusing ${OLD_IMAGE} as base (already has os-ken + matplotlib)"
            base_arg=(--build-arg "BASE=${OLD_IMAGE}")
        fi
        docker build --network host "${base_arg[@]}" -t "${IMAGE}" "${REPO}"
        ;;
    test|smoke|quick|full|suite|analyze|sim|shell) "${RUN[@]}" "$cmd" "$@" ;;
    *)       sed -n '3,15p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac
