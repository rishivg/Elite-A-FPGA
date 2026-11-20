// Independent documented NMOS stream model: exact bus requests and retirement state.
// Read responses are free symbolic bytes, including discarded reads and reads during writes.
// The model never assigns DUT state; reset and the ordinary prefix establish its starting relation.
// Numeric phases are: 0 opcode fetch, 1 operand/DECODE, 2/3 taken-branch dummy reads,
// 4 zero-page data, 5 absolute high operand, 6 absolute/indirect data, 7 indexed dummy,
// 8 zero-page-index dummy, 9/10 pointer bytes, 11 original RMW write, 12 final RMW write.
// 'committed' samples all architectural values immediately after retirement DECODE.
// Intermediate execution flags are not retirement state; every bus request remains checked.
// Independent bus and architectural model for unbounded short/branch streams.
// Read responses stay symbolic; no internal CPU state is initialized by this contract.
module BbcCpuMemoryStreamContract(input clk,output equivalent);
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
always @* assume((twoCycle(firstOpcode)||branch(firstOpcode)||zeroPage(firstOpcode)||absolute(firstOpcode)||zeroX(firstOpcode)||zeroY(firstOpcode)||absoluteX(firstOpcode)||absoluteY(firstOpcode)||indirectX(firstOpcode)||indirectY(firstOpcode))&&(twoCycle(nextOpcode)||branch(nextOpcode)||zeroPage(nextOpcode)||absolute(nextOpcode)||zeroX(nextOpcode)||zeroY(nextOpcode)||absoluteX(nextOpcode)||absoluteY(nextOpcode)||indirectX(nextOpcode)||indirectY(nextOpcode)));
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [3:0] stage=0;
reg [7:0] actualOpcode=0;
reg [7:0] zeroAddress=0,absoluteHigh=0,pointerLow=0,pointerHigh=0;
reg primed=0;
reg executingRmw=0,executingIncDec=0,committed=0;
reg [15:0] rmwAddress=0;
reg [15:0] currentPc=0,fallPc=0,targetPc=0;
reg [7:0] fetchedOpcode=0,executingOpcode=0,executingOperand=0;
wire [7:0] selectedIndex=zeroY(actualOpcode)||(absoluteY(actualOpcode)||indirectY(actualOpcode))?modelY:modelX;
wire [7:0] indexedZero=zeroAddress+selectedIndex;
wire [15:0] indexedAbsolute=(indirectY(actualOpcode)?{pointerHigh,pointerLow}:{absoluteHigh,zeroAddress})+{8'd0,selectedIndex};
reg [7:0] modelA=0,modelX=0,modelY=0,modelS=0,modelP=0;
wire [7:0] nextA,nextX,nextY,nextS,nextP,memoryResult;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @*begin
 expectedAddress=currentPc;returnedData=step==26 ? firstOpcode : nextOpcode;
 case(stage)
 1:begin expectedAddress=currentPc+16'd1;returnedData=nextOperand;end
 2:begin expectedAddress=fallPc;returnedData=nextOperand;end
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
 .expectedA(nextA),.expectedX(nextX),.expectedY(nextY),.expectedS(nextS),.expectedP(nextP),.memoryResult(memoryResult));
wire expectedWrite=stage==11 || stage==12 || (stage==4 || stage==6) && (executingOpcode==8'h81 || executingOpcode==8'h84 || executingOpcode==8'h86);
wire [7:0] expectedValue=stage==11 ? executingOperand : stage==12 ? memoryResult : executingOpcode==8'h84 ? modelY : executingOpcode==8'h86 ? modelX : modelA;
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
  0:begin fetchedOpcode<=step==26 ? firstOpcode : nextOpcode;stage<=1;end
  1:begin
   committed<=1;executingRmw<=rmw(fetchedOpcode);executingIncDec<=semantic(fetchedOpcode)==8'hca || semantic(fetchedOpcode)==8'he8;actualOpcode<=fetchedOpcode;executingOpcode<=semantic(fetchedOpcode);executingOperand<=nextOperand;
   if(primed)begin modelA<=nextA;modelX<=nextX;modelY<=nextY;modelS<=nextS;modelP<=nextP;end
   primed<=1;
   if(indirectX(fetchedOpcode)||indirectY(fetchedOpcode))begin zeroAddress<=nextOperand;currentPc<=nextInstructionPc;stage<=indirectX(fetchedOpcode)?8:9;end
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
