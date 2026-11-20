"""Guard status sampling, READY predicates and direct typed register writes."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

module = ast.parse((ROOT / "tests/validation/test_c_fdc_byte_source.py").read_text())
BASE = next(
    ast.literal_eval(n.value)
    for n in module.body
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "SOURCE" for t in n.targets)
)
SOURCE = (
    BASE.replace(
        "internalCommand:31", "internalCommand:31,internalDriveInCopy:21,internalDriveInLatched:27"
    )
    .replace("selectFlags:192", "selectFlags:192,select_1:128")
    .replace("lateDma:10", "lateDma:10,driveNotReady:16")
    .rstrip()[:-1]
    + """
 _doReadDriveStatus(){
  let driveIn=this._driveIn;
  this._regs[Registers.internalDriveInCopy]=driveIn;
  this._regs[Registers.internalDriveInLatched]|=0xbb;
  driveIn &= this._regs[Registers.internalDriveInLatched];
  this._regs[Registers.internalDriveInLatched]=driveIn;
  return driveIn;
 }
 _checkDriveReady(){
  this._doReadDriveStatus();
  const mask=this._driveOut&DriveOut.select_1?0x40:0x04;
  if(!(this._regs[Registers.internalDriveInLatched]&mask)){
   this._finishCommand(Result.driveNotReady);return false;
  }
  return true;
 }
 _writeRegister(reg,val){reg &= 0x3f;if(reg<IntelFdc.NumRegisters){this._regs[reg]=val;return;}throw 1;}
}
"""
)


class ReadySourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """
import {lowerReady} from './tools/bbc/fdc_ready_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerReady(source);return true;}catch{return false;}})));
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

    def test_bank_aliases_and_ready_masks_are_guarded(self):
        sources = [
            SOURCE,
            SOURCE.replace("Uint8Array", "Uint16Array"),
            SOURCE.replace("internalDriveInCopy:21", "internalDriveInCopy:20"),
            SOURCE.replace("internalDriveInLatched:27", "internalDriveInLatched:26"),
            SOURCE.replace("select_1:128", "select_1:64"),
            SOURCE.replace("driveNotReady:16", "driveNotReady:17"),
            SOURCE.replace("return 32;", "return 64;"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_sampling_has_no_unmodelled_state_or_effects(self):
        sources = [
            SOURCE,
            SOURCE.replace(
                "this._regs[Registers.internalDriveInCopy]=driveIn;",
                "this._regs[Registers.internalDriveInCopy]=this._regs[Registers.internalDriveInCopy];",
            ),
            SOURCE.replace(
                "this._regs[Registers.internalDriveInCopy]=driveIn;",
                "if(driveIn)this._regs[Registers.internalDriveInCopy]=driveIn;",
            ),
            SOURCE.replace(
                "let driveIn=this._driveIn;", "let driveIn=this._driveIn;this._cpu.setNmi(1);"
            ),
            SOURCE.replace(
                "let driveIn=this._driveIn;", "let driveIn=this._driveIn;while(driveIn){}"
            ),
            SOURCE.replace("return driveIn;", "return this._regs[3];"),
            SOURCE.replace("return driveIn;", "return false;"),
            SOURCE.replace(
                "driveIn &= this._regs[Registers.internalDriveInLatched];",
                "driveIn ^= this._regs[Registers.internalDriveInLatched];",
            ),
            SOURCE.replace("return driveIn;", "return Math.random();"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_ready_order_returns_and_write_effects_are_guarded(self):
        sources = [
            SOURCE,
            SOURCE.replace(
                "this._doReadDriveStatus();", "this._doReadDriveStatus();this._doReadDriveStatus();"
            ),
            SOURCE.replace("this._doReadDriveStatus();", "this._unknown();"),
            SOURCE.replace(
                "this._finishCommand(Result.driveNotReady);return false;",
                "this._finishCommand(Result.driveNotReady);return 0;",
            ),
            SOURCE.replace(
                "this._regs[reg]=val;return;", "this._regs[reg]=val;this._setResult(val);return;"
            ),
            SOURCE.replace("reg &= 0x3f", "reg &= 0x1f"),
            SOURCE.replace("const mask=this._driveOut", "const mask=unknown()"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))


if __name__ == "__main__":
    unittest.main()
