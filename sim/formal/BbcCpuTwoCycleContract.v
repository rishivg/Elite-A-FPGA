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
function twoCycle;
 input [7:0] op;
 begin twoCycle=immediate(op) || op==8'hea || op==8'h18 || op==8'h38 || op==8'hd8 || op==8'hf8 || op==8'hb8 || op==8'h58 || op==8'h78 || op==8'hca || op==8'h88 || op==8'he8 || op==8'hc8 || op==8'h8a || op==8'h98 || op==8'haa || op==8'ha8 || op==8'h9a || op==8'hba || op==8'h0a || op==8'h4a || op==8'h2a || op==8'h6a;end
endfunction
always @* assume(twoCycle(firstOpcode) && twoCycle(nextOpcode));
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
  phase<=!phase;offset<=offset+((!phase || immediate(fetchedOpcode)) ? 16'd1 : 16'd0);
  if(!phase)fetchedOpcode<=step==26 ? firstOpcode : nextOpcode;
  else begin
   executingOpcode<=fetchedOpcode;executingOperand<=nextOperand;
   if(primed)begin modelA<=nextA;modelX<=nextX;modelY<=nextY;modelS<=nextS;modelP<=nextP;end
   primed<=1;
  end
 end
end
wire [7:0] visibleP=executingOpcode==8'h78 ? modelP|8'h04 : executingOpcode==8'h58 ? modelP&8'hfb : modelP;
assign equivalent=reset || (busMatches && prefixReady &&
 (step<29 || !phase || (instruction && pc-16'd1==expectedPc)) &&
 (step<30 || phase || (accumulator==modelA && indexX==modelX && indexY==modelY && stack==modelS && (flags&8'hcf)==(visibleP&8'hcf))));
endmodule
