// Two ordinary instructions execute consecutively after the maintained reset
// and register-loading prefix. No internal CPU state is assigned.
module BbcCpuImmediatePairContract(input clk, output equivalent,
 output [7:0] firstOpcode,secondOpcode);
(* anyconst *) reg [7:0] a,x,y,s,p,opcode,second,operand,nextOperand;
(* anyconst *) reg [15:0] base;
// Keep full diagnostic aliases for the non-vacuous opcode admission checks.
assign firstOpcode=opcode;
assign secondOpcode=second;
function immediate;
 input [7:0] op;
 begin immediate = op==8'h09 || op==8'h29 || op==8'h49 ||
 op==8'h69 || op==8'he9 || op==8'hc9 || op==8'he0 ||
 op==8'hc0 || op==8'ha9 || op==8'ha2 || op==8'ha0; end
endfunction
always @* assume(immediate(opcode) && immediate(second));
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @* begin
 expectedAddress=base+17;returnedData=8'hea;
 case(step)
  26:begin expectedAddress=base+13;returnedData=opcode;end
  27:begin expectedAddress=base+14;returnedData=operand;end
  28:begin expectedAddress=base+15;returnedData=second;end
  29:begin expectedAddress=base+16;returnedData=nextOperand;end
  30:begin expectedAddress=base+17;returnedData=8'hea;end
  31:begin expectedAddress=base+18;returnedData=8'hea;end
 endcase
end
wire [7:0] firstA,firstX,firstY,firstS,firstP,unusedFirst;
wire [7:0] secondA,secondX,secondY,secondS,secondP,unusedSecond;
BbcCpuArchitectureContract firstModel(
 .a(a),.x(x),.y(y),.s(s),.p(p),.operand(operand),.opcode(opcode),
 .isRmw(1'b0),.isIncDec(1'b0),.expectedA(firstA),.expectedX(firstX),
 .expectedY(firstY),.expectedS(firstS),.expectedP(firstP),.memoryResult(unusedFirst));
BbcCpuArchitectureContract secondModel(
 .a(firstA),.x(firstX),.y(firstY),.s(firstS),.p(firstP),
 .operand(nextOperand),.opcode(second),.isRmw(1'b0),.isIncDec(1'b0),
 .expectedA(secondA),.expectedX(secondX),.expectedY(secondY),
 .expectedS(secondS),.expectedP(secondP),.memoryResult(unusedSecond));
BbcCpuContractPrefix prefix(
 .clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(opcode),.base(base),
 .targetAddress(expectedAddress),.targetRead(returnedData),
 .targetWrite(1'b0),.targetValue(p),.retirementStep(6'd31),
 .step(step),.reset(reset),.busMatches(busMatches),.prefixReady(prefixReady),
 .writing(writing),.instruction(instruction),.address(address),.pc(pc),
 .value(value),.accumulator(accumulator),.indexX(indexX),.indexY(indexY),
 .stack(stack),.flags(flags),.retiredFlags(retiredFlags));
assign equivalent=reset || (prefixReady && (step>=32 || busMatches) &&
 (step!=29 || (instruction && pc-16'd1==base+16'd15)) &&
 (step!=31 || (instruction && pc-16'd1==base+16'd17)) &&
 (step!=30 || (accumulator==firstA && indexX==firstX && indexY==firstY && stack==firstS && (flags&8'hcf)==(firstP&8'hcf))) &&
 (step!=32 || (accumulator==secondA && indexX==secondX && indexY==secondY && stack==secondS && (flags&8'hcf)==(secondP&8'hcf))));
endmodule
