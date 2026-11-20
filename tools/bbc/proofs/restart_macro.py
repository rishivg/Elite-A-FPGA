"""Add retained warm-request events to the accepted-edge NMI model."""

import re

from tools.bbc.proofs import nmi_macro


def transform(core, wrapper):
    """Seed the queued restart exactly as an intervening disabled block does."""
    core, wrapper = nmi_macro.transform(core, wrapper)
    wrapper = wrapper.replace("input wire offEvt, riseEvt,", "input wire offEvt, riseEvt, offWarm,")
    if wrapper.count("input wire offEvt, riseEvt, offWarm,") != 1:
        raise ValueError("Changed accepted-edge wrapper event ports")
    name = "warm_pending"
    wrapper = re.sub(r"\b" + name + r"\b(?!\s*(?:<=|=(?!=)))", "macro_warm_pending", wrapper)
    declaration = re.search(r"reg warm_pending = 0, warm_second = 0;", wrapper)
    if declaration is None:
        raise ValueError("Changed retained restart declaration")
    wrapper = (
        wrapper[: declaration.end()]
        + "\n    wire macro_warm_pending = warm_pending || offWarm;"
        + wrapper[declaration.end() :]
    )
    capture = "if (warm_reset) warm_pending <= 1;"
    if wrapper.count(capture) != 1:
        raise ValueError("Changed actual restart capture")
    wrapper = wrapper.replace(capture, "if (warm_reset || offWarm) warm_pending <= 1;")
    return core, wrapper
