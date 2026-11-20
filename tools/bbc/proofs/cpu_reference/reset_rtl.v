module SourceRtlReset(input clk,output equivalent,output faultDefined);
(* anyseq *) reg enable;
(* anyseq *) reg [7:0] readData;
reg fresh=1;
reg [3:0] phase=0;
always @(posedge clk)begin
 fresh<=0;
 if(!fresh && enable && phase<11)phase<=phase+1;
end
wire accepted=enable && !fresh && phase<11;
reg [255:0] sourceState=256'hINITIAL_STATE;
reg [63:0] sourcePins=64'hINITIAL_PINS;
wire [255:0] sourceNext;
wire [63:0] sourcePinsNext;
wire trap;
wire [7:0] requestedData=phase==7 ? 8'ha9 : phase==8 ? 8'h40 : phase==9 ? 8'h8d : readData;
reg [7:0] heldResponse=0;
always @(posedge clk)if(accepted)heldResponse<=requestedData;
wire [63:0] response=(sourcePins & ~64'hff0000) | ({56'd0,requestedData}<<16);
M6502ReferenceNext reference(.state_in(sourceState),.pins_in(response),.state_out(sourceNext),.pins_out(sourcePinsNext),.trap(trap));
always @(posedge clk)if(accepted)begin sourceState<=sourceNext;sourcePins<=sourcePinsNext;end
wire [15:0] address,pc;
wire [7:0] writeData,a,x,y,s,p;
wire writeEnable,instruction,sync;
BbcCpu actual(.clk(clk),.reset(fresh),.enable(accepted),.irq(1'b0),.nmi(1'b0),.warm_reset(1'b0),.read_data(heldResponse),.address(address),.write_data(writeData),.write_enable(writeEnable),.instruction(instruction),.sync(sync),.pc(pc),.accumulator(a),.index_x(x),.index_y(y),.stack_pointer(s),.flags(p));
wire busEqual=address==sourcePins[15:0] && writeEnable==!sourcePins[24] && sync==sourcePins[25];
wire finalRegisters=pc==sourcePins[15:0] && a==sourceState[207:200] && x==sourceState[199:192] && y==sourceState[191:184] && s==sourceState[183:176] && (p&8'hcf)==(sourceState[175:168]&8'hcf);
assign equivalent=fresh || (busEqual && instruction==(phase>=8 && previousFetch) && (phase==11 || !trap) && (phase!=11 || finalRegisters));
reg previousFetch=0;
always @(posedge clk)if(accepted)previousFetch<=sourcePins[25];
reg definedHistory=1;
always @(posedge clk)if(accepted)definedHistory<=definedHistory && !trap;
assign faultDefined=!definedHistory || (accepted && trap) || equivalent;
endmodule
