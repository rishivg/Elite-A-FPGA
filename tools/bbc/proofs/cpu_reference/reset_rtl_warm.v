module SourceRtlWarmStartup(input clk,output equivalent,output faultDefined);
(* anyseq *) reg enable,warm;
(* anyseq *) reg [7:0] otherRead;
reg fresh=1,coldReady=0,pendingWarm=0,joined=0;
always @(posedge clk)fresh<=0;
reg [255:0] sourceState=256'hINITIAL_STATE;
reg [63:0] sourcePins=64'hINITIAL_PINS;
wire accepted=enable && !fresh && !joined;
wire restart=coldReady && sourcePins[25] && (pendingWarm || warm);
function [7:0] memoryByte;input [15:0] addr;begin case(addr)
16'hfffc:memoryByte=8'hcd;16'hfffd:memoryByte=8'hd9;
16'hd9cd:memoryByte=8'ha9;16'hd9ce:memoryByte=8'h40;16'hd9cf:memoryByte=8'h8d;
default:memoryByte=otherRead;endcase end endfunction
wire [255:0] nextState;wire [63:0] nextPins;wire trap;
wire [63:0] response=(sourcePins & ~64'hff0000) | ({56'd0,memoryByte(sourcePins[15:0])}<<16) | ({63'd0,restart}<<30);
M6502ReferenceNext reference(.state_in(sourceState),.pins_in(response),.state_out(nextState),.pins_out(nextPins),.trap(trap));
wire [15:0] address,pc;wire [7:0] writeData,a,x,y,s,p;
wire writeEnable,instruction,sync;wire [5:0] microstate;wire [1:0] warmControls,destination;wire [7:0] alu;wire loading,pulling;wire [13:0] interruptState;
reg [7:0] heldResponse=0;
BbcCpu actual(.clk(clk),.reset(fresh),.enable(accepted),.irq(1'b0),.nmi(1'b0),.warm_reset(warm),.read_data(heldResponse),.address(address),.write_data(writeData),.write_enable(writeEnable),.instruction(instruction),.sync(sync),.pc(pc),.accumulator(a),.index_x(x),.index_y(y),.stack_pointer(s),.flags(p),.proofState(microstate),.proofRestartControls(warmControls),.proofAlu(alu),.proofLoad(loading),.proofDestination(destination),.proofPull(pulling),.proofInterruptState(interruptState));
always @(posedge clk)begin
 if(!fresh && warm)pendingWarm<=1;
 if(accepted)begin
  sourceState<=nextState;sourcePins<=nextPins;heldResponse<=memoryByte(address);
  if(sourceState[255:240]==6)coldReady<=1;
  if(restart)pendingWarm<=0;
  if(instruction && pc==16'hd9d0)joined<=1;
 end
end
wire [15:0] ir=sourceState[255:240];
wire shape=(ir==0 && !coldReady && microstate==8) ||
 (ir==1 && microstate==(coldReady ? 12:8)) ||
 (ir==2 && microstate==8) || (ir==3 && microstate==9) ||
 (ir==4 && microstate==10) || (ir==5 && microstate==11) ||
 (ir==6 && microstate==22) || (ir==7 && microstate==23) ||
 (ir==16'h549 && microstate==12) || (ir==16'h54a && microstate==13) ||
 (ir==16'h469 && microstate==12) || (ir==16'h46a && microstate==0);
wire controlShape=pendingWarm==warmControls[1] && warmControls[0]==(coldReady && ir==1) &&
 (p&8'hc9)==0 && (sourceState[175:168]&8'hc9)==0 &&
 p[2]==(coldReady || ir>=6) && sourceState[170]==(coldReady || ir>=5);
wire [7:0] sourceStack=sourceState[183:176];
wire stackRelation=ir==2 ? s==sourceStack+8'd1 :
 ir>=3 && ir<=5 ? s==sourceStack+(ir==3 ? 8'd2:8'd3) : s==sourceStack;
wire aluRelation=(ir==3 || ir==4 ? alu==sourceStack+8'd1 : ir==5 ? alu==sourceStack : 1'b1) &&
 (ir!=16'h469 || (alu==8'h40 && loading && destination==0 && !pulling));
wire operandRelation=(ir!=16'h549 || heldResponse==8'ha9) &&
 (ir!=16'h54a || heldResponse==8'h40) && (ir!=16'h469 || heldResponse==8'h8d);
reg [7:0] baseStack=0;
reg [15:0] resetPc=0;
always @(posedge clk)if(accepted)begin
 if(ir==5)baseStack<=sourceStack;
 if(restart)resetPc<=sourcePins[15:0];
end
wire [7:0] decrements=ir==2 ? 1:ir==3 ? 2:(ir==4 || ir==5) ? 3:0;
wire sourceStackShape=sourceStack==baseStack-decrements && s==baseStack;
wire sourcePcShape=ir==0 ? sourceState[239:224]==0 : ir==1 ? sourceState[239:224]==resetPc :
 ir<=6 ? sourceState[239:224]==resetPc+16'd1 : ir==7 ? sourceState[239:224]==16'hd9cd :
 ir==16'h549 || ir==16'h54a ? sourceState[239:224]==16'hd9cf :
 ir==16'h469 ? sourceState[239:224]==16'hd9d1 : sourceState[239:224]==16'hd9d2;
wire sourceOther=sourceState[199:184]==0 && sourceState[167:104]==sourcePins &&
 sourceState[103:48]==0 && sourceState[39:32]==1 && sourceState[31:0]==0;
wire sourceAd=ir==4 ? sourceState[223:208]==16'hfffc : ir==5 ? sourceState[223:208]==16'hfffd :
 ir>=6 && ir!=16'h46a ? sourceState[223:208]==16'hcd : 1'b1;
wire [7:0] sourceA=sourceState[207:200];
wire afterLda=ir==16'h54a || ir==16'h469 || ir==16'h46a;
wire accumulatorShape=(sourceA==0 || sourceA==8'h40) &&
 (!afterLda || sourceA==8'h40) && sourceState[169]==(sourceA==0) &&
 (a==0 || a==8'h40) && !p[1] &&
 (!(coldReady && ir==1 && loading) || alu==8'h40);
wire busEqual=address==sourcePins[15:0] && writeEnable==!sourcePins[24] && sync==sourcePins[25];
wire architecture=a==sourceState[207:200] && x==sourceState[199:192] && y==sourceState[191:184] && s==sourceState[183:176] && (p&8'hcf)==(sourceState[175:168]&8'hcf);
assign equivalent=fresh || (accumulatorShape && x==0 && y==0 && (!loading || destination==0) && interruptState==0 && shape && controlShape && stackRelation && sourceStackShape && sourcePcShape && sourceOther && sourceAd && aluRelation && operandRelation && busEqual && (joined || !trap) && (!joined || architecture));
reg definedHistory=1;
always @(posedge clk)if(accepted)definedHistory<=definedHistory && !trap;
assign faultDefined=!definedHistory || (accepted && trap) || equivalent;
endmodule
