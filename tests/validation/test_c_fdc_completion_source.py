"""Guard typed completion arithmetic and SPECIFY effect projections."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

# Reuse only the synthetic bank/getter premises, independently of the vendor tree.
module = ast.parse((ROOT / "tests/validation/test_c_fdc_byte_source.py").read_text())
BASE = next(
    ast.literal_eval(n.value)
    for n in module.body
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "SOURCE" for t in n.targets)
)
SOURCE = (
    BASE.replace(
        "internalCommand:31",
        "internalCommand:31,internalParam_3:5,internalParam_2:6,internalParam_4:4,internalPointer:0,internalParamCount:1,internalParam_1:7",
    )
    .replace("selectFlags:192", "selectFlags:192,writeEnable:1")
    .replace("scanDataAndDeleted:1", "scanDataAndDeleted:1,specify:13")
    .replace("lateDma:10", "lateDma:10,ok:0")
    .replace(
        "export class IntelFdc",
        "const ParamAccept=Object.freeze({specify:2});\nexport class IntelFdc",
    )
)
SOURCE = (
    SOURCE.rstrip()[:-1]
    + """
 _checkCompletion(){
  if(!this._checkDriveReady())return;
  this._driveOutLower(DriveOut.writeEnable);
  this._clearCallbacks();
  if((--this._regs[Registers.internalParam_3]&31)===0){this._finishCommand(Result.ok);}
  else{this._regs[Registers.internalParam_2]+=this._regs[Registers.internalParam_4]&63;this._doCommandDispatch();}
 }
 _doCommandDispatch(){
  const command=this._internalCommand;
  switch(command){case Command.specify:
   this._regs[Registers.internalPointer]=this._regs[Registers.internalParam_1];
   this._regs[Registers.internalParamCount]=3;
   this._paramCallback=ParamAccept.specify;
   break;
  }
 }
}
"""
)


class CompletionSourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """
import {lowerCompletion} from './tools/bbc/fdc_completion_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerCompletion(source);return true;}catch{return false;}})));
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

    def test_typed_bank_aliases_and_method_frames(self):
        sources = [
            SOURCE,
            SOURCE.replace("Uint8Array", "Uint16Array"),
            SOURCE.replace("internalParam_3:5", "internalParam_3:6"),
            SOURCE.replace("internalPointer:0", "internalPointer:2"),
            SOURCE.replace("specify:13", "specify:12"),
            SOURCE.replace("_checkCompletion(){", "async _checkCompletion(){"),
            SOURCE.replace("_checkCompletion(){", "_checkCompletion(arg){"),
            SOURCE.replace("return 32;", "return 64;"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_no_unmodelled_arithmetic_or_completion_effects(self):
        sources = [
            SOURCE,
            SOURCE.replace(
                "--this._regs[Registers.internalParam_3]", "this._regs[Registers.internalParam_3]--"
            ),
            SOURCE.replace("&31", "/31"),
            SOURCE.replace(
                "if(!this._checkDriveReady())return;", "if(!this._checkDriveReady())throw 1;"
            ),
            SOURCE.replace(
                "this._clearCallbacks();", "this._clearCallbacks();this._clearCallbacks();"
            ),
            SOURCE.replace("this._doCommandDispatch();", "this._unknown();"),
            SOURCE.replace(
                "this._regs[Registers.internalParam_2]+=", "this._regs[Registers.internalParam_2]="
            ),
            SOURCE.replace("this._clearCallbacks();", "this._clearCallbacks();this._regs[10]=1;"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_specify_projection_rejects_extra_or_changed_effects(self):
        sources = [
            SOURCE,
            SOURCE.replace(
                "this._paramCallback=ParamAccept.specify;",
                "this._paramCallback=ParamAccept.specify;this._state=1;",
            ),
            SOURCE.replace("internalParamCount]=3", "internalParamCount]=2"),
            SOURCE.replace(
                "internalPointer]=this._regs[Registers.internalParam_1]",
                "internalPointer]=this._regs[Registers.internalParam_2]",
            ),
            SOURCE.replace(
                "this._paramCallback=ParamAccept.specify;", "this._paramCallback=other;"
            ),
            SOURCE.replace(
                "const command=this._internalCommand;",
                "const command=this._internalCommand;this._unknown();",
            ),
            SOURCE.replace("switch(command)", "switch(this._internalCommand)"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))


if __name__ == "__main__":
    unittest.main()
