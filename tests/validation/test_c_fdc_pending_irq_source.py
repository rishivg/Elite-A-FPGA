"""Guard pending-byte source prefixes and omitted old-data inputs."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

namespace = {"__name__": "write_fixture"}
exec(
    compile(
        ast.parse((ROOT / "tests/validation/test_c_fdc_write_gap_source.py").read_text()),
        "write_fixture",
        "exec",
    ),
    namespace,
)
SOURCE = (
    namespace["SOURCE"]
    .replace("internalStatus:23", "internalStatus:23,internalData:29")
    .replace("needData:4", "needData:4,nmi:8")
    .replace(
        "if(this._irqCallbacks){this._checkDataLossOk();}",
        "if(this._irqCallbacks){if(!this._checkDataLossOk())return;this._regs[Registers.internalData]=dataByte;this._statusRaise(StatusFlag.nmi|StatusFlag.needData);}",
    )
    .rstrip()[:-1]
    + """
 _byteCallbackWriting(){if(this._irqCallbacks){if(!this._checkDataLossOk())return;this._statusRaise(StatusFlag.nmi|StatusFlag.needData);}switch(this._state){case State.writeRun:this._callbackWriteRun();break;}}
}
"""
)


class PendingPrefixSourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """import {lowerPendingPrefix} from './tools/bbc/fdc_pending_irq_source.mjs';const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerPendingPrefix(source);return true;}catch{return false;}})));"""
        r = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(sources),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(r.stdout)

    def test_aliases_and_write_only_data_effect_are_guarded(self):
        cases = [
            SOURCE,
            SOURCE.replace("internalData:29", "internalData:28"),
            SOURCE.replace("nmi:8", "nmi:16"),
            SOURCE.replace(
                "this._regs[Registers.internalData]=dataByte;",
                "this._regs[Registers.internalData]=this._regs[Registers.internalData];",
            ),
            SOURCE.replace(
                "this._regs[Registers.internalData]=dataByte;",
                "this._regs[Registers.internalData]=dataByte^1;",
            ),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))

    def test_prefix_order_and_short_circuit_are_guarded(self):
        cases = [
            SOURCE,
            SOURCE.replace(
                "if(!this._checkDataLossOk())return;", "if(this._checkDataLossOk())return;"
            ),
            SOURCE.replace(
                "this._regs[Registers.internalData]=dataByte;",
                "this._statusRaise(StatusFlag.nmi);this._regs[Registers.internalData]=dataByte;",
            ),
            SOURCE.replace("_byteCallbackWriting(){", "_byteCallbackWriting(){this._unknown();"),
            SOURCE.replace(
                "if(this._irqCallbacks){if(!this._checkDataLossOk())return;this._statusRaise",
                "if(this._irqCallbacks&&this._state===12){if(!this._checkDataLossOk())return;this._statusRaise",
            ),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))

    def test_write_run_dispatch_is_single_and_static(self):
        cases = [
            SOURCE,
            SOURCE.replace("case State.writeRun:", "case this._unknown():case State.writeRun:"),
            SOURCE.replace("case State.writeRun:", "case 12:case State.writeRun:"),
            SOURCE.replace(
                "this._callbackWriteRun();break;",
                "this._callbackWriteRun();this._callbackWriteRun();break;",
            ),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))


if __name__ == "__main__":
    unittest.main()
