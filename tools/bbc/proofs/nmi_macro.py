"""Build an accepted-edge CPU model with explicit NMI events from intervening waits."""

import re


def transform(core, wrapper):
    """Preserve state, adding event inputs and combinational wait-block seeds."""
    header = core.index(");")
    core = core[:header] + ", BBC_OFF_EVT, BBC_RISE_EVT" + core[header:]
    header = core.index(");") + 2
    core = core[:header] + "\ninput BBC_OFF_EVT, BBC_RISE_EVT;\n" + core[header:]
    for old in ("(NMI && !NMI_1)", "NMI && !NMI_1", "NMI & ~NMI_1"):
        core = core.replace(old, "BBC_RISE_EVT")
    # Preseed the retained edge bits exactly as the proved disabled-block summary.
    for name, available in (
        ("NMI_edge", "bbc_macro_pending"),
        ("bbc_nmi_unaged", "bbc_macro_unaged"),
    ):
        pattern = r"\b" + name + r"\b(?!\s*(?:<=|=(?!=)))"
        core = re.sub(pattern, available, core)
        declaration = re.search(r"^reg " + name + r"\s*=.*?;", core, re.M)
        if declaration is None:
            raise ValueError("Missing original retained NMI register: " + name)
        core = (
            core[: declaration.end()]
            + f"\nwire {available} = {name} || BBC_OFF_EVT;"
            + core[declaration.end() :]
        )
    # When the accepted edge does not acknowledge NMI, retain the wait event.
    target = "else if( BBC_RISE_EVT )\n        NMI_edge <= 1;"
    if core.count(target) != 1:
        raise ValueError("Changed actual NMI pending update")
    core = core.replace(target, "else if( BBC_RISE_EVT || BBC_OFF_EVT )\n        NMI_edge <= 1;")
    wrapper = wrapper.replace("module BbcCpu(", "module BbcCpu(\n    input wire offEvt, riseEvt,")
    target = ".BBC_P(flags));"
    if wrapper.count(target) != 1:
        raise ValueError("Changed actual CPU wrapper binding")
    wrapper = wrapper.replace(target, ".BBC_OFF_EVT(offEvt), .BBC_RISE_EVT(riseEvt), " + target)
    return core, wrapper
