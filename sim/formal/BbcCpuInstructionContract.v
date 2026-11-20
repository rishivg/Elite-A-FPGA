// Documented NMOS bus and retirement contract after a normal reset/prefix.
// Covers 22 implied/accumulator and 11 immediate instructions, 8 branches and BRK.
// Inputs are symbolic bus responses; no internal CPU state is forced.
module BbcCpuInstructionContract(input clk, output equivalent, output [7:0] opcode);
// Preserve the entire symbolic opcode for non-vacuous domain admission.
(* anyconst *) reg [7:0] symbolicOpcode;
assign opcode=symbolicOpcode;
(* anyconst *) reg [7:0] a, x, y, s, p, discarded, operand;
(* anyconst *) reg [7:0] writeRead0, writeRead1, writeRead2;
(* anyconst *) reg [15:0] base,vector;
wire isBrk=opcode==0;
wire isImmediate=opcode==8'h09 || opcode==8'h29 || opcode==8'h49 || opcode==8'h69 || opcode==8'he9 || opcode==8'hc9 || opcode==8'he0 || opcode==8'hc0 || opcode==8'ha9 || opcode==8'ha2 || opcode==8'ha0;
wire isBranch=opcode==8'h10 || opcode==8'h30 || opcode==8'h50 || opcode==8'h70 || opcode==8'h90 || opcode==8'hb0 || opcode==8'hd0 || opcode==8'hf0;
wire branchTaken=opcode==8'h10 ? !p[7] : opcode==8'h30 ? p[7] :
 opcode==8'h50 ? !p[6] : opcode==8'h70 ? p[6] :
 opcode==8'h90 ? !p[0] : opcode==8'hb0 ? p[0] :
 opcode==8'hd0 ? !p[1] : p[1];
wire [15:0] fallPc=base+16'd15;
wire [15:0] targetPc=fallPc+{{8{discarded[7]}},discarded};
wire crossed=fallPc[15:8]!=targetPc[15:8];
wire [1:0] extraCycles=branchTaken ? (crossed ? 2 : 1) : 0;
wire [15:0] returnPc=base+16'd15;
always @* assume(
  isBranch || isImmediate || opcode==0 ||
  opcode==8'hea ||
  opcode==8'h18 ||
  opcode==8'h38 ||
  opcode==8'hd8 ||
  opcode==8'hf8 ||
  opcode==8'hb8 ||
  opcode==8'h58 ||
  opcode==8'h78 ||
  opcode==8'hca ||
  opcode==8'h88 ||
  opcode==8'he8 ||
  opcode==8'hc8 ||
  opcode==8'h8a ||
  opcode==8'h98 ||
  opcode==8'haa ||
  opcode==8'ha8 ||
  opcode==8'h9a ||
  opcode==8'hba ||
  opcode==8'h0a ||
  opcode==8'h4a ||
  opcode==8'h2a ||
  opcode==8'h6a
);
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @* begin
 expectedAddress=0;returnedData=8'hea;
 case(step)
  26: begin expectedAddress=base+13;returnedData=opcode;end
  27: begin expectedAddress=base+14;returnedData=isImmediate ? operand : discarded;end
  // Only BRK consumes these cycles; implied instructions continue with NOP.
 28: begin expectedAddress=16'h0100|{8'd0,s};returnedData=writeRead0;end
 29: begin expectedAddress=16'h0100|{8'd0,(s-8'd1)};returnedData=writeRead1;end
 30: begin expectedAddress=16'h0100|{8'd0,(s-8'd2)};returnedData=writeRead2;end
 31: begin expectedAddress=16'hfffe;returnedData=vector[7:0];end
 32: begin expectedAddress=16'hffff;returnedData=vector[15:8];end
 endcase
 if(isBranch && step==28)expectedAddress=fallPc;
 if(isBranch && step==29)expectedAddress={fallPc[15:8],targetPc[7:0]};
 if(!isBrk && step>=28) returnedData=8'hea;
end
wire [7:0] expectedA,expectedX,expectedY,expectedS,expectedP,memoryResult;
BbcCpuArchitectureContract architecture(
 .a(a),.x(x),.y(y),.s(s),.p(p),.operand(operand),
 .opcode(opcode),
 .isRmw(1'b0),.isIncDec(1'b0),
 .expectedA(expectedA),.expectedX(expectedX),.expectedY(expectedY),
 .expectedS(expectedS),.expectedP(expectedP),.memoryResult(memoryResult)
);
wire [15:0] endPc=isBrk ? vector : isBranch ? (branchTaken ? targetPc : fallPc) : base+(isImmediate ? 16'd15 : 16'd14);
wire expectedWrite=step==15 || (isBrk && step>=28 && step<=30);
wire [7:0] expectedValue=!isBrk ? p : step==28 ? returnPc[15:8] : step==29 ? returnPc[7:0] : step==30 ? p|8'h30 : p;
// The core diagnostic PC points one byte past the current fetch.
wire [15:0] diagnosticPc=pc-16'd1;
wire [5:0] retirementStep=isBrk ? 34 : isBranch ? 29+extraCycles : 29;
BbcCpuContractPrefix prefix(
 .clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(opcode),.base(base),
 .targetAddress(expectedAddress),.targetRead(returnedData),
 .targetWrite(expectedWrite),.targetValue(expectedValue),.retirementStep(retirementStep),
 .step(step),.reset(reset),.busMatches(busMatches),.prefixReady(prefixReady),.writing(writing),.instruction(instruction),
 .address(address),.pc(pc),.value(value),.accumulator(accumulator),.indexX(indexX),.indexY(indexY),
 .stack(stack),.flags(flags),.retiredFlags(retiredFlags)
);
assign equivalent=reset || (prefixReady &&
 (step>=(isBrk ? 33 : isBranch ? 28+extraCycles : 28) || busMatches) &&
 (step!=(isBrk ? 34 : isBranch ? 29+extraCycles : 29) || (instruction && diagnosticPc==endPc)) &&
 (step!=(isBrk ? 35 : isBranch ? 30+extraCycles : 30) || (accumulator==expectedA && indexX==expectedX && indexY==expectedY && stack==(isBrk ? s-8'd3 : expectedS) && (retiredFlags&8'hcf)==((isBrk ? p|8'h04 : expectedP)&8'hcf))));
endmodule
