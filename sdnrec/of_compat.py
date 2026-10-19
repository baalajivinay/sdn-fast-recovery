"""
Import shim so the controllers run on either Ryu or os-ken.

Ryu is unmaintained and no longer installable on a stock Python 3.10 image:
its build needs setuptools < 66 (for pbr's script generation), but setuptools
that old needs `distutils`, which recent images no longer ship. The two
constraints cannot both be satisfied.

os-ken is the OpenStack fork of Ryu -- same architecture, same OpenFlow
parser, same event/decorator API, with the module prefix changed from `ryu.`
to `os_ken.`. It publishes wheels, so nothing has to be built.

os-ken is preferred when present, with Ryu as the fallback, so the same
controller source runs on a lab machine that already has Ryu installed.
Nothing in the experiment depends on which one is used: both speak OpenFlow
1.3 to Open vSwitch, and the recovery behaviour under test lives in the
switch, not the framework.
"""

try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import (
        CONFIG_DISPATCHER,
        MAIN_DISPATCHER,
        set_ev_cls,
    )
    from os_ken.ofproto import ofproto_v1_3

    FRAMEWORK = "os-ken"
except ImportError:  # pragma: no cover - exercised only on Ryu installs
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import (
        CONFIG_DISPATCHER,
        MAIN_DISPATCHER,
        set_ev_cls,
    )
    from ryu.ofproto import ofproto_v1_3

    FRAMEWORK = "ryu"

# The base application class is the one name the fork actually renamed:
# Ryu's `RyuApp` became `OSKenApp`. Everything else -- decorators, event
# names, the OpenFlow parser -- kept its spelling. Controllers subclass
# ControllerApp so they do not have to care which framework is installed.
ControllerApp = getattr(app_manager, "OSKenApp", None) or app_manager.RyuApp

__all__ = [
    "app_manager",
    "ofp_event",
    "CONFIG_DISPATCHER",
    "MAIN_DISPATCHER",
    "set_ev_cls",
    "ofproto_v1_3",
    "ControllerApp",
    "FRAMEWORK",
]
