module BbcCpuImmediateInduction(input clk,output equivalent);
(* anyconst *) reg [7:0] a,x,y,s,p,firstOpcode;
(* anyconst *) reg [15:0] base;
(* anyseq *) reg [7:0] nextOpcode,nextOperand;
function immediate;
 input [7:0] op;
 begin immediate = op==8'h09 || op==8'h29 || op==8'h49 ||
 op==8'h69 || op==8'he9 || op==8'hc9 || op==8'he0 ||
 op==8'hc0 || op==8'ha9 || op==8'ha2 || op==8'ha0; end
endfunction
always @* assume(immediate(firstOpcode) && immediate(nextOpcode));
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg phase=0,primed=0;
reg [15:0] offset=13;
reg [7:0] fetchedOpcode=0,executingOpcode=0,executingOperand=0;
reg [7:0] modelA=0,modelX=0,modelY=0,modelS=0,modelP=0;
wire [7:0] nextA,nextX,nextY,nextS,nextP,unused;
wire [15:0] expectedAddress=base+offset;
wire [15:0] expectedPc=expectedAddress-16'd1;
wire [7:0] returnedData=phase ? nextOperand : step==26 ? firstOpcode : nextOpcode;
BbcCpuArchitectureContract semantics(
 .a(modelA),.x(modelX),.y(modelY),.s(modelS),.p(modelP),
 .operand(executingOperand),.opcode(executingOpcode),.isRmw(1'b0),.isIncDec(1'b0),
 .expectedA(nextA),.expectedX(nextX),.expectedY(nextY),.expectedS(nextS),.expectedP(nextP),.memoryResult(unused));
BbcCpuStreamPrefix prefix(
 .clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(firstOpcode),.base(base),
 .targetAddress(expectedAddress),.targetRead(returnedData),.targetWrite(1'b0),
 .targetValue(p),.retirementStep(6'd29),.step(step),.reset(reset),
 .busMatches(busMatches),.prefixReady(prefixReady),.writing(writing),
 .instruction(instruction),.address(address),.pc(pc),.value(value),
 .accumulator(accumulator),.indexX(indexX),.indexY(indexY),.stack(stack),
 .flags(flags),.retiredFlags(retiredFlags));
always @(posedge clk)begin
 if(reset)begin
  phase<=0;primed<=0;offset<=13;
  modelA<=a;modelX<=x;modelY<=y;modelS<=s;modelP<=p;
 end else if(step>=26)begin
  phase<=!phase;offset<=offset+16'd1;
  if(!phase)fetchedOpcode<=step==26 ? firstOpcode : nextOpcode;
  else begin
   executingOpcode<=fetchedOpcode;executingOperand<=nextOperand;
   if(primed)begin modelA<=nextA;modelX<=nextX;modelY<=nextY;modelS<=nextS;modelP<=nextP;end
   primed<=1;
  end
 end
end
assign equivalent=reset || (busMatches && prefixReady &&
 (step<29 || !phase || (instruction && pc-16'd1==expectedPc)) &&
 (step<30 || phase || (accumulator==modelA && indexX==modelX && indexY==modelY && stack==modelS && (flags&8'hcf)==(modelP&8'hcf))));
endmodule
