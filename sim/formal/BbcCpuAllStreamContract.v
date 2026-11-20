// Independent documented NMOS bus/retirement model for all 151 instructions.
// Ordinary reset/prefix only; IRQ/NMI inactive. No DUT state is assigned.
// Symbolic per-request bytes include discarded reads and reads during writes.
// Stack push/pull prefetch responses are retained explicitly, matching ordinary memory fetches.
// Architectural state is checked after retirement DECODE; every bus request is checked.
// Independent bus and architectural model for unbounded short/branch streams.
// Read responses stay symbolic; no internal CPU state is initialized by this contract.
module BbcCpuAllStreamContract(input clk,output equivalent);
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
function zeroPage;
 input [7:0] op;
 begin zeroPage=op==8'h05 || op==8'h25 || op==8'h45 || op==8'h65 || op==8'he5 || op==8'hc5 || op==8'he4 || op==8'hc4 || op==8'ha5 || op==8'ha6 || op==8'ha4 || op==8'h24 || op==8'h85 || op==8'h84 || op==8'h86 || op==8'h06 || op==8'h26 || op==8'h46 || op==8'h66 || op==8'hc6 || op==8'he6;end
endfunction
function absolute;
 input [7:0] op;
 begin absolute=op==8'h0d || op==8'h2d || op==8'h4d || op==8'h6d || op==8'hed || op==8'hcd || op==8'hec || op==8'hcc || op==8'had || op==8'hae || op==8'hac || op==8'h2c || op==8'h8d || op==8'h8c || op==8'h8e || op==8'h0e || op==8'h2e || op==8'h4e || op==8'h6e || op==8'hce || op==8'hee;end
endfunction
function zeroX;
 input [7:0] op;
 begin zeroX=op==8'h15 || op==8'h35 || op==8'h55 || op==8'h75 || op==8'hf5 || op==8'hd5 || op==8'hb5 || op==8'hb4 || op==8'h94 || op==8'h95 || op==8'h16 || op==8'h36 || op==8'h56 || op==8'h76 || op==8'hd6 || op==8'hf6;end
endfunction
function zeroY;
 input [7:0] op;
 begin zeroY=op==8'hb6 || op==8'h96;end
endfunction
function absoluteX;
 input [7:0] op;
 begin absoluteX=op==8'h1d || op==8'h3d || op==8'h5d || op==8'h7d || op==8'hfd || op==8'hdd || op==8'hbd || op==8'hbc || op==8'h9d || op==8'h1e || op==8'h3e || op==8'h5e || op==8'h7e || op==8'hde || op==8'hfe;end
endfunction
function absoluteY;
 input [7:0] op;
 begin absoluteY=op==8'h19 || op==8'h39 || op==8'h59 || op==8'h79 || op==8'hf9 || op==8'hd9 || op==8'hb9 || op==8'hbe || op==8'h99;end
endfunction
function indirectX;
 input [7:0] op;
 begin indirectX=op==8'h01 || op==8'h21 || op==8'h41 || op==8'h61 || op==8'he1 || op==8'hc1 || op==8'ha1 || op==8'h81;end
endfunction
function indirectY;
 input [7:0] op;
 begin indirectY=op==8'h11 || op==8'h31 || op==8'h51 || op==8'h71 || op==8'hf1 || op==8'hd1 || op==8'hb1 || op==8'h91;end
endfunction
function [7:0] semantic;
 input [7:0] op;
 begin case(op)
8'h05:semantic=8'h09;
8'h25:semantic=8'h29;
8'h45:semantic=8'h49;
8'h65:semantic=8'h69;
8'he5:semantic=8'he9;
8'hc5:semantic=8'hc9;
8'he4:semantic=8'he0;
8'hc4:semantic=8'hc0;
8'ha5:semantic=8'ha9;
8'ha6:semantic=8'ha2;
8'ha4:semantic=8'ha0;
8'h24:semantic=8'h24;
8'h85:semantic=8'h81;
8'h84:semantic=8'h84;
8'h86:semantic=8'h86;
8'h0d:semantic=8'h09;
8'h2d:semantic=8'h29;
8'h4d:semantic=8'h49;
8'h6d:semantic=8'h69;
8'hed:semantic=8'he9;
8'hcd:semantic=8'hc9;
8'hec:semantic=8'he0;
8'hcc:semantic=8'hc0;
8'had:semantic=8'ha9;
8'hae:semantic=8'ha2;
8'hac:semantic=8'ha0;
8'h2c:semantic=8'h24;
8'h8d:semantic=8'h81;
8'h8c:semantic=8'h84;
8'h8e:semantic=8'h86;
8'h15:semantic=8'h09;
8'h35:semantic=8'h29;
8'h55:semantic=8'h49;
8'h75:semantic=8'h69;
8'hf5:semantic=8'he9;
8'hd5:semantic=8'hc9;
8'hb5:semantic=8'ha9;
8'hb4:semantic=8'ha0;
8'h94:semantic=8'h84;
8'h95:semantic=8'h81;
8'hb6:semantic=8'ha2;
8'h96:semantic=8'h86;
8'h1d:semantic=8'h09;
8'h3d:semantic=8'h29;
8'h5d:semantic=8'h49;
8'h7d:semantic=8'h69;
8'hfd:semantic=8'he9;
8'hdd:semantic=8'hc9;
8'hbd:semantic=8'ha9;
8'hbc:semantic=8'ha0;
8'h9d:semantic=8'h81;
8'h19:semantic=8'h09;
8'h39:semantic=8'h29;
8'h59:semantic=8'h49;
8'h79:semantic=8'h69;
8'hf9:semantic=8'he9;
8'hd9:semantic=8'hc9;
8'hb9:semantic=8'ha9;
8'hbe:semantic=8'ha2;
8'h99:semantic=8'h81;
8'h01:semantic=8'h09;
8'h21:semantic=8'h29;
8'h41:semantic=8'h49;
8'h61:semantic=8'h69;
8'he1:semantic=8'he9;
8'hc1:semantic=8'hc9;
8'ha1:semantic=8'ha9;
8'h81:semantic=8'h81;
8'h11:semantic=8'h09;
8'h31:semantic=8'h29;
8'h51:semantic=8'h49;
8'h71:semantic=8'h69;
8'hf1:semantic=8'he9;
8'hd1:semantic=8'hc9;
8'hb1:semantic=8'ha9;
8'h91:semantic=8'h81;
8'h06:semantic=8'h0a;
8'h26:semantic=8'h2a;
8'h46:semantic=8'h4a;
8'h66:semantic=8'h6a;
8'hc6:semantic=8'hca;
8'he6:semantic=8'he8;
8'h16:semantic=8'h0a;
8'h36:semantic=8'h2a;
8'h56:semantic=8'h4a;
8'h76:semantic=8'h6a;
8'hd6:semantic=8'hca;
8'hf6:semantic=8'he8;
8'h0e:semantic=8'h0a;
8'h2e:semantic=8'h2a;
8'h4e:semantic=8'h4a;
8'h6e:semantic=8'h6a;
8'hce:semantic=8'hca;
8'hee:semantic=8'he8;
8'h1e:semantic=8'h0a;
8'h3e:semantic=8'h2a;
8'h5e:semantic=8'h4a;
8'h7e:semantic=8'h6a;
8'hde:semantic=8'hca;
8'hfe:semantic=8'he8;
default:semantic=op;endcase end
endfunction
function rmw;
 input [7:0] op;
 begin rmw=op==8'h06 || op==8'h26 || op==8'h46 || op==8'h66 || op==8'hc6 || op==8'he6 || op==8'h16 || op==8'h36 || op==8'h56 || op==8'h76 || op==8'hd6 || op==8'hf6 || op==8'h0e || op==8'h2e || op==8'h4e || op==8'h6e || op==8'hce || op==8'hee || op==8'h1e || op==8'h3e || op==8'h5e || op==8'h7e || op==8'hde || op==8'hfe;end
endfunction
function control;
 input [7:0] op;
 begin control=op==8'h00 || op==8'h08 || op==8'h48 || op==8'h28 || op==8'h68 || op==8'h4c || op==8'h6c || op==8'h20 || op==8'h60 || op==8'h40;end
endfunction
function push;
 input [7:0] op;
 begin push=op==8'h08 || op==8'h48;end
endfunction
function pull;
 input [7:0] op;
 begin pull=op==8'h28 || op==8'h68;end
endfunction
always @* assume((twoCycle(firstOpcode)||branch(firstOpcode)||zeroPage(firstOpcode)||absolute(firstOpcode)||zeroX(firstOpcode)||zeroY(firstOpcode)||absoluteX(firstOpcode)||absoluteY(firstOpcode)||indirectX(firstOpcode)||indirectY(firstOpcode)||control(firstOpcode))&&(twoCycle(nextOpcode)||branch(nextOpcode)||zeroPage(nextOpcode)||absolute(nextOpcode)||zeroX(nextOpcode)||zeroY(nextOpcode)||absoluteX(nextOpcode)||absoluteY(nextOpcode)||indirectX(nextOpcode)||indirectY(nextOpcode)||control(nextOpcode)));
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [4:0] stage=0;
reg [7:0] actualOpcode=0;
reg [7:0] zeroAddress=0,absoluteHigh=0,pointerLow=0,pointerHigh=0;
reg primed=0;
reg executingRmw=0,executingIncDec=0,committed=0;
reg [15:0] rmwAddress=0;
reg [7:0] heldOpcode=0;
reg [15:0] currentPc=0,fallPc=0,targetPc=0;
reg [7:0] fetchedOpcode=0,executingOpcode=0,executingOperand=0;
wire [7:0] selectedIndex=zeroY(actualOpcode)||(absoluteY(actualOpcode)||indirectY(actualOpcode))?modelY:modelX;
wire [7:0] indexedZero=zeroAddress+selectedIndex;
wire [15:0] indexedAbsolute=(indirectY(actualOpcode)?{pointerHigh,pointerLow}:{absoluteHigh,zeroAddress})+{8'd0,selectedIndex};
reg [7:0] modelA=0,modelX=0,modelY=0,modelS=0,modelP=0;
wire [7:0] semanticA,semanticX,semanticY,semanticS,semanticP,memoryResult;
wire [7:0] nextA=executingOpcode==8'h68 ? executingOperand:semanticA;
wire [7:0] nextX=semanticX,nextY=semanticY;
wire [7:0] nextS=executingOpcode==8'h00 ? modelS-8'd3:push(executingOpcode)?modelS-8'd1:pull(executingOpcode)?modelS+8'd1:executingOpcode==8'h20 ? modelS-8'd2:executingOpcode==8'h60 ? modelS+8'd2:executingOpcode==8'h40 ? modelS+8'd3:semanticS;
wire [7:0] nextP=executingOpcode==8'h00 ? modelP|8'h04:executingOpcode==8'h28 || executingOpcode==8'h40 ? executingOperand:executingOpcode==8'h68 ? (modelP&8'h7d)|(executingOperand[7]?8'h80:8'h00)|(executingOperand==0?8'h02:8'h00):semanticP;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @*begin
 expectedAddress=currentPc;returnedData=step==26 ? firstOpcode : push(actualOpcode)||pull(actualOpcode)?heldOpcode:nextOpcode;
 case(stage)
 1:begin expectedAddress=currentPc+16'd1;returnedData=push(fetchedOpcode)||pull(fetchedOpcode)?nextOpcode:nextOperand;end
 2:begin expectedAddress=fallPc;returnedData=nextOperand;end
 20:begin expectedAddress={8'h01,modelS};returnedData=nextOperand;end
 21:begin expectedAddress={8'h01,modelS-8'd1};returnedData=nextOperand;end
 22:begin expectedAddress={8'h01,modelS-8'd2};returnedData=nextOperand;end
 23:begin expectedAddress=16'hfffe;returnedData=nextOperand;end
 24:begin expectedAddress=16'hffff;returnedData=nextOperand;end
 13:begin expectedAddress=currentPc+16'd2;returnedData=nextOperand;end
 14:begin expectedAddress={absoluteHigh,zeroAddress};returnedData=nextOperand;end
 15:begin expectedAddress={absoluteHigh,zeroAddress+8'd1};returnedData=nextOperand;end
 16:begin expectedAddress={8'h01,modelS};returnedData=nextOperand;end
 17:begin expectedAddress={8'h01,actualOpcode==8'h20 ? modelS:modelS+8'd1};returnedData=nextOperand;end
 18:begin expectedAddress={8'h01,actualOpcode==8'h20 ? modelS-8'd1:modelS+8'd2};returnedData=nextOperand;end
 19:begin expectedAddress=actualOpcode==8'h60 ? {pointerHigh,pointerLow}:{8'h01,modelS+8'd3};returnedData=nextOperand;end
 11,12:begin expectedAddress=rmwAddress;returnedData=nextOperand;end
 9:begin expectedAddress={8'd0,indirectX(actualOpcode)?indexedZero:zeroAddress};returnedData=nextOperand;end
 10:begin expectedAddress={8'd0,(indirectX(actualOpcode)?indexedZero:zeroAddress)+8'd1};returnedData=nextOperand;end
 7:begin expectedAddress={indirectY(actualOpcode)?pointerHigh:absoluteHigh,indexedAbsolute[7:0]};returnedData=nextOperand;end
 8:begin expectedAddress={8'd0,zeroAddress};returnedData=nextOperand;end
 5:begin expectedAddress=currentPc-16'd1;returnedData=nextOperand;end
 6:begin expectedAddress=indirectX(actualOpcode)?{pointerHigh,pointerLow}:absoluteX(actualOpcode)||absoluteY(actualOpcode)||indirectY(actualOpcode)?indexedAbsolute:{absoluteHigh,zeroAddress};returnedData=nextOperand;end
 4:begin expectedAddress={8'd0,zeroX(actualOpcode)||zeroY(actualOpcode)?indexedZero:zeroAddress};returnedData=nextOperand;end
 3:begin expectedAddress={fallPc[15:8],targetPc[7:0]};returnedData=nextOperand;end
 endcase
end
wire [15:0] nextInstructionPc=currentPc+((absolute(fetchedOpcode)||absoluteX(fetchedOpcode)||absoluteY(fetchedOpcode))?16'd3:(immediate(fetchedOpcode)||branch(fetchedOpcode)||zeroPage(fetchedOpcode)||zeroX(fetchedOpcode)||zeroY(fetchedOpcode)||indirectX(fetchedOpcode)||indirectY(fetchedOpcode)?16'd2:16'd1));
wire [15:0] nextTarget=nextInstructionPc+{{8{nextOperand[7]}},nextOperand};
BbcCpuArchitectureContract semantics(
 .a(modelA),.x(modelX),.y(modelY),.s(modelS),.p(modelP),
 .operand(executingOperand),.opcode(executingOpcode),.isRmw(executingRmw),.isIncDec(executingIncDec),
 .expectedA(semanticA),.expectedX(semanticX),.expectedY(semanticY),.expectedS(semanticS),.expectedP(semanticP),.memoryResult(memoryResult));
wire expectedWrite=stage==20 || stage==21 || stage==22 || (stage==16 && push(actualOpcode)) || ((stage==17 || stage==18)&&actualOpcode==8'h20) || stage==11 || stage==12 || (stage==4 || stage==6) && (executingOpcode==8'h81 || executingOpcode==8'h84 || executingOpcode==8'h86);
wire [15:0] returnPc=currentPc+16'd2;
wire [7:0] expectedValue=stage==20?returnPc[15:8]:stage==21?returnPc[7:0]:stage==22?modelP|8'h30:stage==16 && push(actualOpcode)?(actualOpcode==8'h08 ? modelP|8'h30:modelA):actualOpcode==8'h20 && stage==17?returnPc[15:8]:actualOpcode==8'h20 && stage==18?returnPc[7:0]:stage==11 ? executingOperand : stage==12 ? memoryResult : executingOpcode==8'h84 ? modelY : executingOpcode==8'h86 ? modelX : modelA;
BbcCpuStreamPrefix prefix(
 .clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(firstOpcode),.base(base),
 .targetAddress(expectedAddress),.targetRead(returnedData),.targetWrite(expectedWrite),
 .targetValue(expectedValue),.retirementStep(6'd29),.step(step),.reset(reset),
 .busMatches(busMatches),.prefixReady(prefixReady),.writing(writing),
 .instruction(instruction),.address(address),.pc(pc),.value(value),
 .accumulator(accumulator),.indexX(indexX),.indexY(indexY),.stack(stack),
 .flags(flags),.retiredFlags(retiredFlags));
always @(posedge clk)begin
 committed<=0;
 if(reset)begin
  executingRmw<=0;executingIncDec<=0;rmwAddress<=0;
  stage<=0;primed<=0;currentPc<=base+16'd13;fallPc<=0;targetPc<=0;
  modelA<=a;modelX<=x;modelY<=y;modelS<=s;modelP<=p;
 end else if(step>=26)begin
  case(stage)
  0:begin fetchedOpcode<=step==26 ? firstOpcode : push(actualOpcode)||pull(actualOpcode)?heldOpcode:nextOpcode;stage<=1;end
  1:begin
   committed<=1;executingRmw<=rmw(fetchedOpcode);executingIncDec<=semantic(fetchedOpcode)==8'hca || semantic(fetchedOpcode)==8'he8;actualOpcode<=fetchedOpcode;executingOpcode<=semantic(fetchedOpcode);executingOperand<=nextOperand;
   if(primed)begin modelA<=nextA;modelX<=nextX;modelY<=nextY;modelS<=nextS;modelP<=nextP;end
   primed<=1;
   if(control(fetchedOpcode))begin
    zeroAddress<=nextOperand;heldOpcode<=nextOpcode;
    if(fetchedOpcode==8'h00)stage<=20;
    else if(fetchedOpcode==8'h4c || fetchedOpcode==8'h6c)stage<=13;
    else begin stage<=16;if(push(fetchedOpcode)||pull(fetchedOpcode))currentPc<=currentPc+16'd1;end
   end else if(indirectX(fetchedOpcode)||indirectY(fetchedOpcode))begin zeroAddress<=nextOperand;currentPc<=nextInstructionPc;stage<=indirectX(fetchedOpcode)?8:9;end
   else if(absolute(fetchedOpcode)||absoluteX(fetchedOpcode)||absoluteY(fetchedOpcode))begin zeroAddress<=nextOperand;currentPc<=nextInstructionPc;stage<=5;end
   else if(zeroX(fetchedOpcode)||zeroY(fetchedOpcode))begin zeroAddress<=nextOperand;currentPc<=nextInstructionPc;stage<=8;end
   else if(zeroPage(fetchedOpcode))begin zeroAddress<=nextOperand;currentPc<=nextInstructionPc;stage<=4;end
   else if(branch(fetchedOpcode)&&taken(fetchedOpcode,primed?nextP:modelP))begin
    fallPc<=nextInstructionPc;targetPc<=nextTarget;stage<=2;
   end else begin currentPc<=nextInstructionPc;stage<=0;end
  end
  2:begin
   currentPc<=targetPc;
   if(fallPc[15:8]!=targetPc[15:8])stage<=3;else stage<=0;
  end
  3:stage<=0;
  4:begin executingOperand<=nextOperand;rmwAddress<=expectedAddress;stage<=executingRmw?11:0;end
  5:begin absoluteHigh<=nextOperand;
   if((absoluteX(actualOpcode)||absoluteY(actualOpcode)) && ({1'b0,zeroAddress}+{1'b0,selectedIndex}>9'd255 || executingOpcode==8'h81 || executingRmw))stage<=7;else stage<=6;end
  6:begin executingOperand<=nextOperand;rmwAddress<=expectedAddress;stage<=executingRmw?11:0;end
  7:stage<=6;
  8:stage<=indirectX(actualOpcode)?9:4;
  20:stage<=21;
  21:stage<=22;
  22:stage<=23;
  23:begin pointerLow<=nextOperand;stage<=24;end
  24:begin currentPc<={nextOperand,pointerLow};stage<=0;end
  13:begin
   if(actualOpcode==8'h6c)begin absoluteHigh<=nextOperand;stage<=14;end
   else begin currentPc<={nextOperand,zeroAddress};stage<=0;end
  end
  14:begin pointerLow<=nextOperand;stage<=15;end
  15:begin currentPc<={nextOperand,pointerLow};stage<=0;end
  16:stage<=push(actualOpcode)?0:17;
  17:begin
   if(pull(actualOpcode))begin executingOperand<=nextOperand;stage<=0;end
   else begin if(actualOpcode==8'h40)executingOperand<=nextOperand;else pointerLow<=nextOperand;stage<=18;end
  end
  18:begin
   if(actualOpcode==8'h20)stage<=13;
   else begin if(actualOpcode==8'h40)pointerLow<=nextOperand;else pointerHigh<=nextOperand;stage<=19;end
  end
  19:begin currentPc<=actualOpcode==8'h60 ? {pointerHigh,pointerLow}+16'd1:{nextOperand,pointerLow};stage<=0;end
  11:stage<=12;
  12:stage<=0;
  9:begin pointerLow<=nextOperand;stage<=10;end
  10:begin pointerHigh<=nextOperand;
   if(indirectY(actualOpcode)&&({1'b0,pointerLow}+{1'b0,selectedIndex}>9'd255 || executingOpcode==8'h81 || executingRmw))stage<=7;else stage<=6;end
  endcase
 end
end
wire [7:0] visibleP=executingOpcode==8'h78 ? modelP|8'h04 : executingOpcode==8'h58 ? modelP&8'hfb : modelP;
assign equivalent=reset || (busMatches && prefixReady &&
 (step<29 || stage!=1 || (instruction && pc-16'd1==currentPc)) &&
 (step<30 || !committed || (accumulator==modelA && indexX==modelX && indexY==modelY && stack==modelS && (flags&8'hcf)==(visibleP&8'hcf))));
endmodule
