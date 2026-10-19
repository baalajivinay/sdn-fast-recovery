# Testbed image: Mininet 2.3 + Open vSwitch 2.17 + os-ken (Ryu fork).
#
# BASE defaults to the prebuilt Mininet image the previous project used
# (Ubuntu 22.04, Mininet 2.3.0, OVS 2.17.5). run.sh passes
# BASE=sdn-recovery:latest instead when that older image is still on the
# machine: it already carries os-ken and matplotlib, so nothing has to be
# downloaded and the build takes seconds.
ARG BASE=iwaseyusuke/mininet:latest
FROM ${BASE}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# The Mininet base image configures a CA path but ships no bundle; run.sh
# copies the host's bundle here so HTTPS (pip) works without disabling TLS.
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# os-ken, not Ryu: Ryu no longer installs on Python 3.10 (setuptools/distutils
# conflict). os-ken is the maintained OpenStack fork with the same API.
RUN if python3 -c "import os_ken, matplotlib" 2>/dev/null; then \
        echo "base image already has os-ken + matplotlib"; \
    else \
        (python3 -m pip --version 2>/dev/null || \
          (curl -sS --max-time 300 -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py \
           && python3 /tmp/get-pip.py --no-cache-dir && rm -f /tmp/get-pip.py)) \
        && python3 -m pip install --no-cache-dir os-ken "matplotlib==3.7.5"; \
    fi

# Fail the build, not the experiment, if something essential is missing.
RUN python3 -c "from os_ken.ofproto import ofproto_v1_3 as o; assert o.OFPGT_FF == 3; print('os-ken OK, OFPGT_FF available')" \
    && ovs-vsctl --version | head -1 \
    && python3 -c "import mininet.net; print('mininet OK')" \
    && tc -V \
    && python3 -m pip freeze > /opt/frozen-requirements.txt

WORKDIR /work
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
