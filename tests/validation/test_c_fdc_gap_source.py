"""Guard typed read-gap counter effects and supported source inputs."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

module = ast.parse((ROOT / "tests/validation/test_c_fdc_ready_source.py").read_text())
# The READY fixture builds its synthetic bank/getter source independently of the vendor tree.
namespace = {"__name__": "ready_fixture"}
exec(compile(module, "ready_fixture", "exec"), namespace)
BASE = namespace["SOURCE"]
SOURCE = (
    BASE.replace("internalDriveInLatched:27", "internalDriveInLatched:27,internalGap2Skip:21")
    .replace(
        "export class IntelFdc",
        "const State=Object.freeze({skipGap_2:11,syncingForData:7});\nconst Call=Object.freeze({read:4,write:5});\nexport class IntelFdc",
    )
    .rstrip()[:-1]
    + """
 _byteCallbackReading(dataByte,clockByte){
  const command=this._internalCommand;
  if(this._irqCallbacks){this._checkDataLossOk();}
  switch(this._state){case State.skipGap_2:
   if(--this._regs[Registers.internalGap2Skip])break;
   if(this._callContext===Call.read)this._setState(State.syncingForData);
   else if(this._callContext===Call.write)this._doWriteRun(Call.write,0);
   else throw new Error(`Unexpected call context ${this._callContext}`);
   break;
  }
 }
}
"""
)


class GapSourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """
import {lowerGap} from './tools/bbc/fdc_gap_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerGap(source);return true;}catch{return false;}})));
"""
        r = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(sources),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(r.stdout)

    def test_bank_aliases_and_contexts(self):
        cases = [
            SOURCE,
            SOURCE.replace("internalGap2Skip:21", "internalGap2Skip:20"),
            SOURCE.replace("skipGap_2:11", "skipGap_2:12"),
            SOURCE.replace("read:4", "read:3"),
            SOURCE.replace("write:5", "write:6"),
            SOURCE.replace("Uint8Array", "Uint16Array"),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))

    def test_no_unmodelled_reads_or_effects(self):
        cases = [
            SOURCE,
            SOURCE.replace(
                "--this._regs[Registers.internalGap2Skip]",
                "this._regs[Registers.internalGap2Skip]--",
            ),
            SOURCE.replace("if(--this._regs[Registers.internalGap2Skip])", "if(this._regs[0])"),
            SOURCE.replace(
                "this._setState(State.syncingForData);",
                "this._setState(State.syncingForData);this._unknown();",
            ),
            SOURCE.replace(
                "this._doWriteRun(Call.write,0)", "this._doWriteRun(Call.write,Math.random())"
            ),
            SOURCE.replace(
                "const command=this._internalCommand;",
                "const command=this._internalCommand;this._unknown();",
            ),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))

    def test_break_and_trap_inventory(self):
        cases = [
            SOURCE,
            SOURCE.replace("])break;", "])return;"),
            SOURCE.replace(
                "else throw new Error(`Unexpected call context ${this._callContext}`);",
                "else this._finishCommand(18);",
            ),
            SOURCE.replace("Unexpected call context ", "Unexpected "),
            SOURCE.replace("this._doWriteRun(Call.write,0)", "this._doWriteRun(Call.write,0,1)"),
            SOURCE.replace("case State.skipGap_2:", "case State.skipGap_2: case State.skipGap_2:"),
            SOURCE.replace("case State.skipGap_2:", "case this._unknown(): case State.skipGap_2:"),
            SOURCE.replace("case State.skipGap_2:", "case 11: case State.skipGap_2:"),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))


if __name__ == "__main__":
    unittest.main()
