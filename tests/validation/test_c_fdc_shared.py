"""Guard canonical state, actual mutations and source-lowering boundaries."""

import copy
import json
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_shared
from tools.validation.common import ROOT

SOURCE = """
const Registers=Object.freeze({internalPointer:0,internalParamCount:1,internalSeekRetryCount:1,
 internalParam_1:7,badTrack_1Drive_0:16,badTrack_1Drive_1:24,internalSeekTarget_1:11,internalParameter:30});
const Call=Object.freeze({unchanged:1});
const DriveOut=Object.freeze({select_1:128});
const ParamAccept=Object.freeze({none:0,command:1,specify:2});
export class IntelFdc {
 static get NumRegisters(){return 32;}
 _resultConsumed(){}
 _finishSimpleCommand(){}
 _setDriveOut(){}
 _writeRegister(reg,val){reg&=0x3f;if(reg<IntelFdc.NumRegisters){this._regs[reg]=val;return;}}
 _paramWritten(param){
  this._regs[Registers.internalParameter]=param;this._resultConsumed();
  switch(this._paramCallback){
   case ParamAccept.none:break;
   case ParamAccept.command:{this._writeRegister(this._regs[Registers.internalPointer],param);
    --this._regs[Registers.internalPointer];if(--this._regs[Registers.internalParamCount]===0)this._startCommand();break;}
   case ParamAccept.specify:{this._writeRegister(this._regs[Registers.internalPointer],param);
    ++this._regs[Registers.internalPointer];if(--this._regs[Registers.internalParamCount]===0)this._finishSimpleCommand();break;}
   default:throw new Error("invalid");
  }
 }
 _doSeek(callContext){
  if(callContext!==Call.unchanged)this._callContext=callContext;
  let newTrack=this._regs[Registers.internalParam_1]+this._regs[Registers.internalSeekRetryCount];
  const trackRegOffset=this._driveOut&DriveOut.select_1?Registers.badTrack_1Drive_1:Registers.badTrack_1Drive_0;
  if(newTrack>0){if(this._regs[trackRegOffset+0]<=newTrack)newTrack=(newTrack+1)&0xff;
   if(this._regs[trackRegOffset+1]<=newTrack)newTrack=(newTrack+1)&0xff;}
  this._regs[Registers.internalSeekTarget_1]=newTrack;
 }
}
"""


class SharedFdcTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1000 + fdc_shared.WIDTH))
        offset = 0
        fields = {}
        for name, width in fdc_shared.FIELDS.items():
            fields[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": fields,
        }

    def test_state_inventory_rejects_private_or_incomplete_state(self):
        fdc_shared.indices(self.body())
        for fault in ("private", "missing", "alias", "narrow"):
            body = copy.deepcopy(self.body())
            if fault == "private":
                body["netnames"]["seekRetry"] = {"bits": [99999, 99998]}
            elif fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "alias":
                body["netnames"]["register0"] = body["netnames"]["parameterCount"]
            else:
                body["netnames"]["indexCount"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_shared.indices(body)

    def test_inventory_has_all_interference_histories(self):
        self.assertEqual(len(fdc_shared.NAMES), 38)
        self.assertEqual(len(set(fdc_shared.NAMES)), 38)
        self.assertTrue(set(fdc_shared.NATIVE_FAULTS.values()).issubset(fdc_shared.NAMES))

    def test_faults_fail_if_actual_targets_change(self):
        for name in fdc_shared.NATIVE_FAULTS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                fdc_shared.fault("", name)

    def test_owner_control_changes_both_crc_retirement_sites(self):
        target = "parameterCount <= nextRetryCount;"
        self.assertEqual(
            fdc_shared.fault(target * 2, "stale-owner").count("mechanicalOp <= op;"), 2
        )
        for count in (0, 1, 3):
            with self.subTest(count=count), self.assertRaises(ValueError):
                fdc_shared.fault(target * count, "stale-owner")

    def test_native_controls_require_named_observable_difference(self):
        row = "FDC_EPOCH_MISMATCH r1-1--1 cycle 882983 return 4/5 nmi 0/0 edges 0/0\n"
        self.assertTrue(
            fdc_shared.negative_result(subprocess.CompletedProcess([], 1, row, ""), "stale-counter")
        )
        for code, out, err in (
            (0, row, ""),
            (2, row, ""),
            (1, row, "timeout"),
            (1, row.replace("4/5", "5/5"), ""),
            (1, "Missing fixture\n", ""),
            (1, row.replace("r1-1--1", "other"), ""),
        ):
            self.assertFalse(
                fdc_shared.negative_result(
                    subprocess.CompletedProcess([], code, out, err), "stale-counter"
                )
            )

    def test_lowerers_reject_changed_aliases_effects_and_number_overflow(self):
        script = """
import {lowerSeek} from './tools/bbc/fdc_seek_source.mjs';
import {lowerParameter} from './tools/bbc/fdc_parameter_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let data='';process.stdin.on('data',x=>data+=x);process.stdin.on('end',()=>resolve(data));}));
const out=sources.map(([kind,text])=>{try{(kind==='seek'?lowerSeek:lowerParameter)(text);return true;}catch{return false;}});
process.stdout.write(JSON.stringify(out));
"""
        cases = [
            ("seek", SOURCE),
            ("parameter", SOURCE),
            ("seek", SOURCE.replace("internalSeekRetryCount:1", "internalSeekRetryCount:2")),
            ("seek", SOURCE.replace("newTrack+1", "newTrack+2147483647")),
            ("seek", SOURCE.replace("newTrack+1", "newTrack^1")),
            ("parameter", SOURCE.replace("reg&=0x3f", "reg&=0x1f")),
            (
                "parameter",
                SOURCE.replace("_resultConsumed(){}", "_resultConsumed(){this._regs[0]=1;}"),
            ),
            ("parameter", SOURCE.replace("_setDriveOut(){}", "_setDriveOut(){this._regs[1]=0;}")),
            (
                "parameter",
                SOURCE.replace("_resultConsumed();", "_resultConsumed();this._unknown();"),
            ),
            ("parameter", SOURCE.replace("return 32;", "return 64;")),
        ]
        trial = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(cases),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(trial.stdout), [True, True] + [False] * 8)


if __name__ == "__main__":
    unittest.main()
