// Documented NMOS memory addressing, bus cycles and retirement contract.
module BbcCpuMemoryContract(input clk,output equivalent, output [7:0] opcode);
// Preserve the entire symbolic opcode for non-vacuous domain admission.
(* anyconst *) reg [7:0] symbolicOpcode;
assign opcode=symbolicOpcode;
(* anyconst *) reg [7:0] a,x,y,s,p,operand,addressLow,addressHigh,pointerLow,pointerHigh,discarded,writeRead0,writeRead1;
(* anyconst *) reg [15:0] base;
reg [3:0] mode;
reg [7:0] semanticOpcode;
reg isRmw,isStore,isIncDec,supported;
always @* begin
 mode=0;semanticOpcode=0;isRmw=0;isStore=0;isIncDec=0;supported=1;
 case(opcode)
 8'h01:begin mode=7;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h05:begin mode=1;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h06:begin mode=1;semanticOpcode=8'h0a;isRmw=1;isStore=0;isIncDec=0;end
 8'h0d:begin mode=4;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h0e:begin mode=4;semanticOpcode=8'h0a;isRmw=1;isStore=0;isIncDec=0;end
 8'h11:begin mode=8;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h15:begin mode=2;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h16:begin mode=2;semanticOpcode=8'h0a;isRmw=1;isStore=0;isIncDec=0;end
 8'h19:begin mode=6;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h1d:begin mode=5;semanticOpcode=8'h09;isRmw=0;isStore=0;isIncDec=0;end
 8'h1e:begin mode=5;semanticOpcode=8'h0a;isRmw=1;isStore=0;isIncDec=0;end
 8'h21:begin mode=7;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h24:begin mode=1;semanticOpcode=8'h24;isRmw=0;isStore=0;isIncDec=0;end
 8'h25:begin mode=1;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h26:begin mode=1;semanticOpcode=8'h2a;isRmw=1;isStore=0;isIncDec=0;end
 8'h2c:begin mode=4;semanticOpcode=8'h24;isRmw=0;isStore=0;isIncDec=0;end
 8'h2d:begin mode=4;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h2e:begin mode=4;semanticOpcode=8'h2a;isRmw=1;isStore=0;isIncDec=0;end
 8'h31:begin mode=8;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h35:begin mode=2;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h36:begin mode=2;semanticOpcode=8'h2a;isRmw=1;isStore=0;isIncDec=0;end
 8'h39:begin mode=6;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h3d:begin mode=5;semanticOpcode=8'h29;isRmw=0;isStore=0;isIncDec=0;end
 8'h3e:begin mode=5;semanticOpcode=8'h2a;isRmw=1;isStore=0;isIncDec=0;end
 8'h41:begin mode=7;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h45:begin mode=1;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h46:begin mode=1;semanticOpcode=8'h4a;isRmw=1;isStore=0;isIncDec=0;end
 8'h4d:begin mode=4;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h4e:begin mode=4;semanticOpcode=8'h4a;isRmw=1;isStore=0;isIncDec=0;end
 8'h51:begin mode=8;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h55:begin mode=2;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h56:begin mode=2;semanticOpcode=8'h4a;isRmw=1;isStore=0;isIncDec=0;end
 8'h59:begin mode=6;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h5d:begin mode=5;semanticOpcode=8'h49;isRmw=0;isStore=0;isIncDec=0;end
 8'h5e:begin mode=5;semanticOpcode=8'h4a;isRmw=1;isStore=0;isIncDec=0;end
 8'h61:begin mode=7;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h65:begin mode=1;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h66:begin mode=1;semanticOpcode=8'h6a;isRmw=1;isStore=0;isIncDec=0;end
 8'h6d:begin mode=4;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h6e:begin mode=4;semanticOpcode=8'h6a;isRmw=1;isStore=0;isIncDec=0;end
 8'h71:begin mode=8;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h75:begin mode=2;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h76:begin mode=2;semanticOpcode=8'h6a;isRmw=1;isStore=0;isIncDec=0;end
 8'h79:begin mode=6;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h7d:begin mode=5;semanticOpcode=8'h69;isRmw=0;isStore=0;isIncDec=0;end
 8'h7e:begin mode=5;semanticOpcode=8'h6a;isRmw=1;isStore=0;isIncDec=0;end
 8'h81:begin mode=7;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'h84:begin mode=1;semanticOpcode=8'h84;isRmw=0;isStore=1;isIncDec=0;end
 8'h85:begin mode=1;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'h86:begin mode=1;semanticOpcode=8'h86;isRmw=0;isStore=1;isIncDec=0;end
 8'h8c:begin mode=4;semanticOpcode=8'h84;isRmw=0;isStore=1;isIncDec=0;end
 8'h8d:begin mode=4;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'h8e:begin mode=4;semanticOpcode=8'h86;isRmw=0;isStore=1;isIncDec=0;end
 8'h91:begin mode=8;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'h94:begin mode=2;semanticOpcode=8'h84;isRmw=0;isStore=1;isIncDec=0;end
 8'h95:begin mode=2;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'h96:begin mode=3;semanticOpcode=8'h86;isRmw=0;isStore=1;isIncDec=0;end
 8'h99:begin mode=6;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'h9d:begin mode=5;semanticOpcode=8'h81;isRmw=0;isStore=1;isIncDec=0;end
 8'ha1:begin mode=7;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'ha4:begin mode=1;semanticOpcode=8'ha0;isRmw=0;isStore=0;isIncDec=0;end
 8'ha5:begin mode=1;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'ha6:begin mode=1;semanticOpcode=8'ha2;isRmw=0;isStore=0;isIncDec=0;end
 8'hac:begin mode=4;semanticOpcode=8'ha0;isRmw=0;isStore=0;isIncDec=0;end
 8'had:begin mode=4;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'hae:begin mode=4;semanticOpcode=8'ha2;isRmw=0;isStore=0;isIncDec=0;end
 8'hb1:begin mode=8;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'hb4:begin mode=2;semanticOpcode=8'ha0;isRmw=0;isStore=0;isIncDec=0;end
 8'hb5:begin mode=2;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'hb6:begin mode=3;semanticOpcode=8'ha2;isRmw=0;isStore=0;isIncDec=0;end
 8'hb9:begin mode=6;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'hbc:begin mode=5;semanticOpcode=8'ha0;isRmw=0;isStore=0;isIncDec=0;end
 8'hbd:begin mode=5;semanticOpcode=8'ha9;isRmw=0;isStore=0;isIncDec=0;end
 8'hbe:begin mode=6;semanticOpcode=8'ha2;isRmw=0;isStore=0;isIncDec=0;end
 8'hc1:begin mode=7;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hc4:begin mode=1;semanticOpcode=8'hc0;isRmw=0;isStore=0;isIncDec=0;end
 8'hc5:begin mode=1;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hc6:begin mode=1;semanticOpcode=8'hca;isRmw=1;isStore=0;isIncDec=1;end
 8'hcc:begin mode=4;semanticOpcode=8'hc0;isRmw=0;isStore=0;isIncDec=0;end
 8'hcd:begin mode=4;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hce:begin mode=4;semanticOpcode=8'hca;isRmw=1;isStore=0;isIncDec=1;end
 8'hd1:begin mode=8;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hd5:begin mode=2;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hd6:begin mode=2;semanticOpcode=8'hca;isRmw=1;isStore=0;isIncDec=1;end
 8'hd9:begin mode=6;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hdd:begin mode=5;semanticOpcode=8'hc9;isRmw=0;isStore=0;isIncDec=0;end
 8'hde:begin mode=5;semanticOpcode=8'hca;isRmw=1;isStore=0;isIncDec=1;end
 8'he1:begin mode=7;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'he4:begin mode=1;semanticOpcode=8'he0;isRmw=0;isStore=0;isIncDec=0;end
 8'he5:begin mode=1;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'he6:begin mode=1;semanticOpcode=8'he8;isRmw=1;isStore=0;isIncDec=1;end
 8'hec:begin mode=4;semanticOpcode=8'he0;isRmw=0;isStore=0;isIncDec=0;end
 8'hed:begin mode=4;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'hee:begin mode=4;semanticOpcode=8'he8;isRmw=1;isStore=0;isIncDec=1;end
 8'hf1:begin mode=8;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'hf5:begin mode=2;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'hf6:begin mode=2;semanticOpcode=8'he8;isRmw=1;isStore=0;isIncDec=1;end
 8'hf9:begin mode=6;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'hfd:begin mode=5;semanticOpcode=8'he9;isRmw=0;isStore=0;isIncDec=0;end
 8'hfe:begin mode=5;semanticOpcode=8'he8;isRmw=1;isStore=0;isIncDec=1;end
 default:supported=0;
 endcase
end
always @* assume(supported);
wire [7:0] selectedIndex=mode==3 || mode==6 || mode==8 ? y : x;
wire [7:0] indexedZero=addressLow+selectedIndex;
wire [15:0] absoluteAddress={addressHigh,addressLow};
wire [15:0] pointerAddress={pointerHigh,pointerLow};
wire [15:0] startAddress=mode==8 ? pointerAddress : absoluteAddress;
wire [15:0] indexedAddress=startAddress+{8'd0,selectedIndex};
wire crossed=startAddress[15:8]!=indexedAddress[15:8];
wire needsExtra=crossed || isStore || isRmw;
wire [3:0] duration=mode==1 ? (isRmw ? 5 : 3) :
 mode==2 || mode==3 ? (isRmw ? 6 : 4) :
 mode==4 ? (isRmw ? 6 : 4) :
 mode==5 || mode==6 ? (isRmw ? 7 : needsExtra ? 5 : 4) :
 mode==7 ? 6 : needsExtra ? 6 : 5;
wire [5:0] memoryStep=mode==1 ? 28 : mode==2 || mode==3 || mode==4 ? 29 :
 mode==5 || mode==6 ? (needsExtra ? 30 : 29) : mode==7 ? 31 : needsExtra ? 31 : 30;
wire [15:0] memoryAddress=mode==1 ? {8'd0,addressLow} :
 mode==2 || mode==3 ? {8'd0,indexedZero} : mode==4 ? absoluteAddress :
 mode==7 ? pointerAddress : indexedAddress;
wire [15:0] provisionalAddress={startAddress[15:8],indexedAddress[7:0]};
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @* begin
 expectedAddress=0;returnedData=8'hea;
 case(step)
 26:begin expectedAddress=base+13;returnedData=opcode;end
 27:begin expectedAddress=base+14;returnedData=addressLow;end
 28:begin
  if(mode==2 || mode==3 || mode==7)begin expectedAddress={8'd0,addressLow};returnedData=discarded;end
  else if(mode==8)begin expectedAddress={8'd0,addressLow};returnedData=pointerLow;end
  else begin expectedAddress=base+15;returnedData=addressHigh;end
 end
 29:begin
  if(mode==7)begin expectedAddress={8'd0,indexedZero};returnedData=pointerLow;end
  else if(mode==8)begin expectedAddress={8'd0,addressLow+8'd1};returnedData=pointerHigh;end
  else begin expectedAddress=provisionalAddress;returnedData=discarded;end
 end
 30:begin
  if(mode==7)begin expectedAddress={8'd0,indexedZero+8'd1};returnedData=pointerHigh;end
  else begin expectedAddress=provisionalAddress;returnedData=discarded;end
 end
 endcase
 if(step==memoryStep)begin expectedAddress=memoryAddress;returnedData=isStore ? writeRead0 : operand;end
 if(isRmw && step==memoryStep+1)begin expectedAddress=memoryAddress;returnedData=writeRead0;end
 if(isRmw && step==memoryStep+2)begin expectedAddress=memoryAddress;returnedData=writeRead1;end
 if(step>=26+duration)returnedData=8'hea;
end
wire [7:0] expectedA,expectedX,expectedY,expectedS,expectedP,memoryResult;
BbcCpuArchitectureContract architecture(
 .a(a),.x(x),.y(y),.s(s),.p(p),.operand(operand),
 .opcode(semanticOpcode),
 .isRmw(isRmw),.isIncDec(isIncDec),
 .expectedA(expectedA),.expectedX(expectedX),.expectedY(expectedY),
 .expectedS(expectedS),.expectedP(expectedP),.memoryResult(memoryResult)
);
wire expectedWrite=(isStore && step==memoryStep) || (isRmw && (step==memoryStep+1 || step==memoryStep+2));
wire [7:0] storeValue=semanticOpcode==8'h84 ? y : semanticOpcode==8'h86 ? x : a;
wire [7:0] expectedValue=isRmw ? (step==memoryStep+1 ? operand : memoryResult) : storeValue;
wire [15:0] endPc=base+(mode>=4 && mode<=6 ? 16'd16 : 16'd15);
wire [15:0] diagnosticPc=pc-16'd1;
wire [5:0] retirementStep=27+duration;
BbcCpuContractPrefix prefix(
 .clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(opcode),.base(base),
 .targetAddress(expectedAddress),.targetRead(returnedData),
 .targetWrite(expectedWrite),.targetValue(expectedValue),.retirementStep(retirementStep),
 .step(step),.reset(reset),.busMatches(busMatches),.prefixReady(prefixReady),.writing(writing),.instruction(instruction),
 .address(address),.pc(pc),.value(value),.accumulator(accumulator),.indexX(indexX),.indexY(indexY),
 .stack(stack),.flags(flags),.retiredFlags(retiredFlags)
);
assign equivalent=reset || (prefixReady &&
 (step>=26+duration || busMatches) &&
 (step!=retirementStep || (instruction && diagnosticPc==endPc)) &&
 (step!=retirementStep+1 || (accumulator==expectedA && indexX==expectedX && indexY==expectedY && stack==s && (retiredFlags&8'hcf)==(expectedP&8'hcf))));
endmodule
