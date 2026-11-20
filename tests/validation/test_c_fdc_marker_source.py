"""Guard typed marker decisions and the result/IRQ/status API frames."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

_tree = ast.parse((ROOT / "tests/validation/test_c_fdc_byte_source.py").read_text())
_base = ast.literal_eval(
    next(
        n.value
        for n in _tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(x, ast.Name) and x.id == "SOURCE" for x in n.targets)
    )
)
_base = (
    _base.replace("internalCountLsb:19", "internalResult:22,internalCountLsb:19")
    .replace("needData:4", "needData:4,busy:128,nmi:8,resultReady:16")
    .replace("lateDma:10", "lateDma:10,clockError:8,flagDeletedData:32")
)
_methods = """
 _writeRegister(reg,val){reg&=0x3f;if(reg<IntelFdc.NumRegisters){this._regs[reg]=val;return;}return;}
 _startIrqCallbacks(){this._regs[Registers.internalStatus]|=0x30;}
 _setResult(result){this._regs[Registers.internalResult]=result;this._isResultReady=true;this._updateExternalStatus();}
 _updateExternalStatus(){let status=this.internalStatus&(StatusFlag.busy|StatusFlag.nmi|StatusFlag.needData);if(this._isResultReady)status|=StatusFlag.resultReady;this._status=status;}
 _byteCallbackReading(dataByte,clockByte){
  const command=this._internalCommand;
  if(this._irqCallbacks){}
  switch(this._state){
   case State.checkDataMarker:
    if(clockByte===IbmDiscFormat.markClockPattern&&(dataByte===IbmDiscFormat.dataMarkDataPattern||dataByte===IbmDiscFormat.deletedDataMarkDataPattern)){
     let doIrqs=true;
     if(dataByte===IbmDiscFormat.deletedDataMarkDataPattern){if((this._regs[Registers.internalCommand]&0x0f)===0)doIrqs=false;this._setResult(Result.flagDeletedData);}
     if(this._regs[Registers.internalCommand]===0x1c)doIrqs=false;
     if(doIrqs)this._startIrqCallbacks();
     this._crc=IbmDiscFormat.crcAddByte(IbmDiscFormat.crcInit(false),dataByte);
     this._setState(State.inData);
    }else{this._finishCommand(Result.clockError);}
    break;
  }
 }
"""
_end = _base.rfind("\n}")
SOURCE = (
    "const State=Object.freeze({checkDataMarker:8,inData:9});\n"
    + _base[:_end]
    + _methods
    + _base[_end:]
)
DISC = "export class IbmDiscFormat{static get markClockPattern(){return 199;}static get dataMarkDataPattern(){return 251;}static get deletedDataMarkDataPattern(){return 248;}}"


class MarkerSourceTests(unittest.TestCase):
    def lower(self, cases):
        script = """
import {lowerMarker} from './tools/bbc/fdc_marker_source.mjs';
const cases=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify(cases.map(([source,disc])=>{try{lowerMarker(source,disc);return true;}catch{return false;}})));
"""
        r = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(cases),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(r.stdout)

    def test_typed_bank_aliases_and_write_prefix_are_fixed(self):
        cases = [
            SOURCE,
            SOURCE.replace("Uint8Array", "Uint16Array"),
            SOURCE.replace("return 32;", "return 64;"),
            SOURCE.replace("internalResult:22", "internalResult:21"),
            SOURCE.replace("reg&=0x3f", "reg&=0x1f"),
        ]
        self.assertEqual(
            self.lower([(s, DISC) for s in cases]), [True] + [False] * (len(cases) - 1)
        )

    def test_effects_and_delegation_cannot_expand_silently(self):
        cases = [
            SOURCE,
            SOURCE.replace("let doIrqs=true", "let doIrqs=1"),
            SOURCE.replace(
                "this._setState(State.inData);", "this._unknown();this._setState(State.inData);"
            ),
            SOURCE.replace(
                "this._setState(State.inData);",
                "this._setState(State.inData);this._setState(State.inData);",
            ),
            SOURCE.replace("crcInit(false)", "crcInit(true)"),
        ]
        self.assertEqual(
            self.lower([(s, DISC) for s in cases]), [True] + [False] * (len(cases) - 1)
        )

    def test_status_and_ready_helpers_preserve_their_frames(self):
        cases = [
            (SOURCE, DISC),
            (SOURCE.replace("this._isResultReady=true", "this._isResultReady=false"), DISC),
            (
                SOURCE.replace(
                    "this._status=status;", "this._status=status;this._cpu.setNmi(1,true);"
                ),
                DISC,
            ),
            (SOURCE.replace("|=0x30;", "|=0x20;"), DISC),
            (SOURCE, DISC.replace("return 199;", "return 199+0;")),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))


if __name__ == "__main__":
    unittest.main()
