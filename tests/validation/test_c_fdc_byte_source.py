"""Guard pure byte-control getters, effect boundaries and typed counter writes."""

import json
import subprocess
import unittest

from tools.validation.common import ROOT

SOURCE = """
const Registers=Object.freeze({internalCountLsb:19,internalCountMsb:20,internalStatus:23,mode:23,internalCommand:31});
const DriveOut=Object.freeze({selectFlags:192});
const FdcMode=Object.freeze({noDma:1});
const Command=Object.freeze({scanData:0,scanDataAndDeleted:1});
const StatusFlag=Object.freeze({needData:4});
const Result=Object.freeze({lateDma:10});
export class IntelFdc {
 constructor(){this._regs=new Uint8Array(IntelFdc.NumRegisters);}
 static get NumRegisters(){return 32;}
 get internalStatus(){return this._regs[Registers.internalStatus];}
 get _internalCommand(){return (this._regs[Registers.internalCommand]&~(DriveOut.selectFlags|3))>>>2;}
 get _irqCallbacks(){return (this._regs[Registers.internalStatus]&0x30)===0x30;}
 _stopIrqCallbacks(){this._regs[Registers.internalStatus]&=~0x30;}
 _decrementCounter(){
  if(--this._regs[Registers.internalCountLsb])return false;
  --this._regs[Registers.internalCountMsb];
  if(this._regs[Registers.internalCountMsb]!==255){this._regs[Registers.internalCountLsb]=128;return false;}
  this._regs[Registers.internalCountMsb]=0;
  this._stopIrqCallbacks();return true;
 }
 _checkDataLossOk(){
  let ok=true;
  if(!(this._regs[Registers.mode]&FdcMode.noDma))ok=false;
  const command=this._internalCommand;
  if(command===Command.scanData||command===Command.scanDataAndDeleted)ok=false;
  if(this.internalStatus&StatusFlag.needData)ok=false;
  if(ok)return true;
  this._commandAbort();this._finishCommand(Result.lateDma);return false;
 }
}
"""


class ByteSourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """
import {lowerByteControl} from './tools/bbc/fdc_byte_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerByteControl(source);return true;}catch{return false;}})));
"""
        trial = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(sources),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(trial.stdout)

    def test_bank_aliases_and_boolean_returns_must_remain_exact(self):
        sources = [
            SOURCE,
            SOURCE.replace("internalCountLsb:19", "internalCountLsb:18"),
            SOURCE.replace("mode:23", "mode:22"),
            SOURCE.replace("Uint8Array", "Uint16Array"),
            SOURCE.replace("return 32;", "return 64;"),
            SOURCE.replace("return 32;", "throw 32;"),
            SOURCE.replace("return true;", "return 1;"),
            SOURCE.replace("===0x30", "&0x30"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_projection_rejects_external_effects_and_unmodelled_number_operations(self):
        sources = [
            SOURCE,
            SOURCE.replace("this._commandAbort();", "this._unknown();"),
            SOURCE.replace(
                "this._finishCommand(Result.lateDma);", "this._finishCommand(unknown());"
            ),
            SOURCE.replace(
                "if(--this._regs[Registers.internalCountLsb])",
                "if(this._regs[Registers.internalCountLsb]--)",
            ),
            SOURCE.replace(
                "this._regs[Registers.internalCountMsb]=0;",
                "this._regs[Registers.internalCountMsb]+=1;",
            ),
            SOURCE.replace("let ok=true;", "let ok=true;while(ok){}"),
            SOURCE.replace("let ok=true;", "let ok=true;this._regs[Registers.internalStatus]=0;"),
            SOURCE.replace(
                "if(--this._regs[Registers.internalCountLsb])",
                "if(1+this._regs[Registers.internalCountLsb])",
            ),
            SOURCE.replace(
                "if(--this._regs[Registers.internalCountLsb])",
                "if(this._regs[Registers.internalCountLsb]==1)",
            ),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_each_finite_truth_table_must_cover_every_read_input(self):
        sources = [
            SOURCE,
            SOURCE.replace(
                "if(--this._regs[Registers.internalCountLsb])", "if(this._internalCommand)"
            ),
            SOURCE.replace(
                "let ok=true;", "const count=this._regs[Registers.internalCountLsb];let ok=true;"
            ),
            SOURCE.replace(
                "get _irqCallbacks(){return",
                "get _irqCallbacks(){this._regs[Registers.internalStatus]=0;return",
            ),
            SOURCE.replace(
                "(this._regs[Registers.internalStatus]&0x30)",
                "(this._regs[Registers.internalCommand]&0x30)",
            ),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))


if __name__ == "__main__":
    unittest.main()
