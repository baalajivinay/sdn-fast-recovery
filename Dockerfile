# Self-contained replication image: Mininet 2.3 + Open vSwitch 2.17.5 +
# os-ken 4.2.1, with the experiment code baked in.
#
#   docker build -t sdn-fast-recovery .
#   docker run --rm -it --privileged -v /lib/modules:/lib/modules:ro \
#       -v "$PWD/results:/work/results" sdn-fast-recovery smoke
#
# The base is pinned by digest (Ubuntu 22.04, Mininet 2.3.0, OVS 2.17.5).
# run.sh passes BASE=sdn-recovery:latest when that older local image exists:
# it already has os-ken + matplotlib, so the build takes seconds.
ARG BASE=iwaseyusuke/mininet@sha256:1e063a3cc7552212e037290a504120b7acc2bbf4c2b402cf67e05cff0ae21dc1
FROM ${BASE}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# The base image ships no CA bundle, so HTTPS (get-pip, PyPI) fails without it.
# Taking Alpine's bundle avoids a slow apt-get update for one file.
COPY --from=alpine:3.20 /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# os-ken, not Ryu: Ryu no longer installs on Python 3.10 (setuptools/distutils
# conflict). os-ken is the maintained OpenStack fork with the same API.
# constraints.txt pins every dependency to the versions of the measured run.
COPY docker/constraints.txt /opt/constraints.txt
RUN if python3 -c "import os_ken, matplotlib" 2>/dev/null; then \
        echo "base image already has os-ken + matplotlib"; \
    else \
        (python3 -m pip --version 2>/dev/null || \
          (curl -sS --max-time 300 -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py \
           && python3 /tmp/get-pip.py --no-cache-dir && rm -f /tmp/get-pip.py)) \
        && python3 -m pip install --no-cache-dir -c /opt/constraints.txt os-ken matplotlib; \
    fi

# Fail the build, not the experiment, if something essential is missing.
RUN python3 -c "from os_ken.ofproto import ofproto_v1_3 as o; assert o.OFPGT_FF == 3; print('os-ken OK, OFPGT_FF available')" \
    && ovs-vsctl --version | head -1 \
    && python3 -c "import mininet.net; print('mininet OK')" \
    && tc -V \
    && python3 -m pip freeze > /opt/frozen-requirements.txt

WORKDIR /work
COPY sdnrec/ sdnrec/
COPY experiments/ experiments/
COPY sim/ sim/
COPY tests/ tests/
COPY docs/REPLICATION.md docs/
COPY results/summary.json reference/summary.json
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && python3 -m compileall -q sdnrec experiments sim tests

LABEL org.opencontainers.image.source="https://github.com/baalajivinay/sdn-fast-recovery" \
      org.opencontainers.image.description="Replication of van Adrichem et al. (EWSDN 2014) SDN fast recovery on Mininet + OVS"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["help"]
