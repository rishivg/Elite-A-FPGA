// Independent bus and architectural model for unbounded short/branch streams.
// Read responses stay symbolic; no internal CPU state is initialized by this contract.
module BbcCpuBranchStreamContract(input clk,output equivalent);
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
function branch;
 input [7:0] op;
 begin branch=op==8'h10 || op==8'h30 || op==8'h50 || op==8'h70 || op==8'h90 || op==8'hb0 || op==8'hd0 || op==8'hf0;end
endfunction
function taken;
 input [7:0] op,status;
 begin case(op)
 8'h10:taken=!status[7];8'h30:taken=status[7];8'h50:taken=!status[6];8'h70:taken=status[6];
 8'h90:taken=!status[0];8'hb0:taken=status[0];8'hd0:taken=!status[1];8'hf0:taken=status[1];default:taken=0;
 endcase end
endfunction
always @* assume((twoCycle(firstOpcode)||branch(firstOpcode))&&(twoCycle(nextOpcode)||branch(nextOpcode)));
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [1:0] stage=0;
reg primed=0;
reg [15:0] currentPc=0,fallPc=0,targetPc=0;
reg [7:0] fetchedOpcode=0,executingOpcode=0,executingOperand=0;
reg [7:0] modelA=0,modelX=0,modelY=0,modelS=0,modelP=0;
wire [7:0] nextA,nextX,nextY,nextS,nextP,unused;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @*begin
 expectedAddress=currentPc;returnedData=step==26 ? firstOpcode : nextOpcode;
 case(stage)
 1:begin expectedAddress=currentPc+16'd1;returnedData=nextOperand;end
 2:begin expectedAddress=fallPc;returnedData=nextOperand;end
 3:begin expectedAddress={fallPc[15:8],targetPc[7:0]};returnedData=nextOperand;end
 endcase
end
wire [15:0] nextInstructionPc=currentPc+(immediate(fetchedOpcode)||branch(fetchedOpcode)?16'd2:16'd1);
wire [15:0] nextTarget=nextInstructionPc+{{8{nextOperand[7]}},nextOperand};
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
  stage<=0;primed<=0;currentPc<=base+16'd13;fallPc<=0;targetPc<=0;
  modelA<=a;modelX<=x;modelY<=y;modelS<=s;modelP<=p;
 end else if(step>=26)begin
  case(stage)
  0:begin fetchedOpcode<=step==26 ? firstOpcode : nextOpcode;stage<=1;end
  1:begin
   executingOpcode<=fetchedOpcode;executingOperand<=nextOperand;
   if(primed)begin modelA<=nextA;modelX<=nextX;modelY<=nextY;modelS<=nextS;modelP<=nextP;end
   primed<=1;
   if(branch(fetchedOpcode)&&taken(fetchedOpcode,primed?nextP:modelP))begin
    fallPc<=nextInstructionPc;targetPc<=nextTarget;stage<=2;
   end else begin currentPc<=nextInstructionPc;stage<=0;end
  end
  2:begin
   currentPc<=targetPc;
   if(fallPc[15:8]!=targetPc[15:8])stage<=3;else stage<=0;
  end
  3:stage<=0;
  endcase
 end
end
wire [7:0] visibleP=executingOpcode==8'h78 ? modelP|8'h04 : executingOpcode==8'h58 ? modelP&8'hfb : modelP;
assign equivalent=reset || (busMatches && prefixReady &&
 (step<29 || stage!=1 || (instruction && pc-16'd1==currentPc)) &&
 (step<30 || stage==1 || (accumulator==modelA && indexX==modelX && indexY==modelY && stack==modelS && (flags&8'hcf)==(visibleP&8'hcf))));
endmodule
