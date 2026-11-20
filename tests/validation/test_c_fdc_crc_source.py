"""Guard the typed bank, CRC body and explicit call-boundary projections."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

_fixture = ast.parse((ROOT / "tests/validation/test_c_fdc_byte_source.py").read_text())
_base = ast.literal_eval(
    next(
        n.value
        for n in _fixture.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SOURCE" for t in n.targets)
    )
)
_base = (
    _base.replace(
        "internalCountLsb:19",
        "internalSeekRetryCount:1,internalHeaderPointer:8,internalIdTrack:12,internalIdSector:10,internalParam_1:7,"
        "internalParam_2:6,internalGap2Skip:21,internalCountLsb:19",
    )
    .replace("lateDma:10", "lateDma:10,idCrcError:12,sectorNotFound:24")
    .replace("needData:4", "needData:4,commandFull:64")
)
_methods = """
 _commandWritten(command){const status=this.internalStatus;if(status&128){}this._regs[31]=command;this._statusRaise(128);this._statusLower(StatusFlag.commandFull);}
 _readRegister(reg){reg&=0x3f;if(reg<IntelFdc.NumRegisters){return this._regs[reg];}return 0;}
 _checkCrc(error){if(this._crc===this._onDiscCrc)return true;this._finishCommand(error);return false;}
 _byteCallbackReading(dataByte,clockByte){
  const command=this._internalCommand;
  if(this._irqCallbacks){}
  switch(this._state){
   case State.inId:
    this._crc=IbmDiscFormat.crcAddByte(this._crc,dataByte);
    this._writeRegister(this._regs[Registers.internalHeaderPointer],dataByte);
    this._regs[Registers.internalHeaderPointer]--;
    if((this._regs[Registers.internalHeaderPointer]&7)===0){
     this._onDiscCrc=0;this._stopIrqCallbacks();this._setState(State.inIdCrc);
    }
    break;
   case State.inIdCrc:
    this._onDiscCrc=((this._onDiscCrc<<8)|dataByte)&0xffffffff;
    if(++this._stateCount===2){
     if(!this._checkCrc(Result.idCrcError)){break;}
     if(this._regs[Registers.internalCommand]===0x18){this._checkCompletion();}
     else if(this._regs[Registers.internalIdTrack]!==this._regs[Registers.internalParam_1]){
      if(++this._regs[Registers.internalSeekRetryCount]===3){this._finishCommand(Result.sectorNotFound);}
      else{this._logCommand("retry");this._doSeek(Call.unchanged);}
     }else if(this._regs[Registers.internalIdSector]===this._regs[Registers.internalParam_2]){
      this._regs[Registers.internalGap2Skip]=11;
      if(this._callContext===Call.write){this._regs[Registers.internalCountMsb]=0;this._regs[Registers.internalCountLsb]=5;}
      this._setState(State.skipGap_2);
     }else{this._setState(State.syncingForIdWait);}
    }
    break;
  }
 }
"""
_end = _base.rfind("\n}")
SOURCE = (
    "const State=Object.freeze({inId:5,inIdCrc:6,skipGap_2:11,syncingForIdWait:2});\n"
    "const Call=Object.freeze({unchanged:1,seek:2,readId:3,read:4,write:5});\n"
    + _base[:_end]
    + _methods
    + _base[_end:]
)


class CrcSourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """
import {lowerIdCrc} from './tools/bbc/fdc_crc_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerIdCrc(source);return true;}catch{return false;}})));
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

    def test_bank_and_direct_status_read_must_remain_typed_and_complete(self):
        sources = [
            SOURCE,
            SOURCE.replace("Uint8Array", "Uint16Array"),
            SOURCE.replace("return 32;", "return 64;"),
            SOURCE.replace("internalHeaderPointer:8", "internalHeaderPointer:9"),
            SOURCE.replace("return this._regs[reg];", "return this._regs[reg]&0xbf;"),
            SOURCE.replace("reg<IntelFdc.NumRegisters", "reg<16"),
            SOURCE.replace("commandFull:64", "commandFull:32"),
            SOURCE.replace(
                "_statusLower(StatusFlag.commandFull)", "_statusRaise(StatusFlag.commandFull)"
            ),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_crc_projection_rejects_new_inputs_effects_and_number_operations(self):
        sources = [
            SOURCE,
            SOURCE.replace("this._doSeek(Call.unchanged)", "this._unknown(Call.unchanged)"),
            SOURCE.replace("if(++this._stateCount===2)", "if(this._stateCount++===2)"),
            SOURCE.replace("if(++this._stateCount===2)", "if(this._stateCount+1===2)"),
            SOURCE.replace("this._onDiscCrc<<8", "this._unknown<<8"),
            SOURCE.replace(
                "!==this._regs[Registers.internalParam_1]",
                "!==this._regs[Registers.internalHeaderPointer]",
            ),
            SOURCE.replace(
                "const command=this._internalCommand;",
                "this._other();const command=this._internalCommand;",
            ),
            SOURCE.replace(
                "return true;this._finishCommand(error)", "return 1;this._finishCommand(error)"
            ),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))

    def test_header_tail_preserves_postdecrement_and_static_status_support(self):
        sources = [
            SOURCE,
            SOURCE.replace("internalHeaderPointer]--;", "internalHeaderPointer]++;"),
            SOURCE.replace(
                "this._stopIrqCallbacks();this._setState(State.inIdCrc);",
                "this._other();this._stopIrqCallbacks();this._setState(State.inIdCrc);",
            ),
            SOURCE.replace("&=~0x30", "&=this._regs[Registers.internalCountLsb]"),
            SOURCE.replace("this._setState(State.inIdCrc)", "this._setState(State.skipGap_2)"),
        ]
        self.assertEqual(self.lower(sources), [True] + [False] * (len(sources) - 1))


if __name__ == "__main__":
    unittest.main()
