#!/usr/bin/env python3
"""
Minimal stand-in for `ryu-manager` / `osken-manager`.

The os-ken wheel on PyPI ships the library only -- there is no `os_ken.cmd`
package and no console script, so there is nothing to launch an application
with. Everything needed is still there (`AppManager.run_apps`, the oslo.config
CLI options), it just has no entry point wired up.

This reproduces what ryu-manager's main() does, and takes the same arguments,
so the experiment driver can invoke it interchangeably with a real
ryu-manager on a machine that has one:

    python3 run_controller.py --ofp-tcp-listen-port 6653 protection.py

Importing os_ken.flags is what registers --ofp-tcp-listen-port and friends;
it looks unused but removing it breaks argument parsing.
"""

import sys

from os_ken import cfg
from os_ken import flags  # noqa: F401  (registers CLI options on import)
from os_ken import log
from os_ken.base.app_manager import AppManager
from oslo_config import cfg as oslo_cfg


def main():
    conf = cfg.CONF

    # os_ken.flags registers the transport options (--ofp-tcp-listen-port and
    # so on), but the options naming the applications to run were declared in
    # Ryu's cmd/manager.py, which os-ken does not ship. Without these, passing
    # an app on the command line fails with "unrecognized arguments".
    conf.register_cli_opts([
        oslo_cfg.ListOpt("app-lists", default=[],
                         help="application module names to run"),
        oslo_cfg.MultiStrOpt("app", positional=True, default=[],
                             help="application module name to run"),
    ])

    conf(project="os_ken", args=sys.argv[1:])
    log.init_log()

    # `app_lists` comes from --app-lists, `app` from positional arguments;
    # ryu-manager concatenates both and so do we.
    app_lists = list(conf.app_lists) + list(conf.app)
    if not app_lists:
        sys.exit("no controller application given")

    AppManager.run_apps(app_lists)


if __name__ == "__main__":
    main()
