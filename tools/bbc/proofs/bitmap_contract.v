// Independent bitmap specification at physical master edges. CRTC behavior
// comes from the separately proved imperative character-history contract.
module BitmapReference #(parameter MULTIPLIER=1)(input clk,reset,
 input crtcWrite,lightPenPulse,ulaWrite,address,input [7:0] data,memoryData,
 input [1:0] scroll,output [354:0] proofState,output [269:0] observations);
reg [4:0] index,master;
reg [1:0] divider;
reg [7:0] control,cellHorizontal;
reg [127:0] regs;
reg [13:0] lightPen;
reg [63:0] palette;
reg [9:0] cellLine;
reg cellDisplay,cellCursor;
reg [2:0] cursorIndex;
wire [143:0] registers={lightPen[7:0],2'b0,lightPen[13:8],regs};
wire [7:0] r1=regs[15:8],r8=regs[71:64];
wire [1:0] skew=r8[5:4];
wire clock32=MULTIPLIER==1 || divider==2;
wire charEnable=clock32 && (control[4]?master[3:0]==15:master==31);
wire fetch=clock32 && (control[4]?master[3:0]==0:master==16);
wire [105:0] crtcState;
wire [13:0] busAddress;
wire busSync,frame;
CrtcHistoryReference crtc(.clk(clk),.reset(reset),.enable(charEnable),
 .registers(registers),.state(crtcState),.busAddress(busAddress),
 .busVsync(busSync),.frame(frame));
wire [7:0] h;
wire [6:0] row;
wire [4:0] raster,adjust,count;
wire [9:0] line;
wire [13:0] lineAddress,nextLineAddress,ma;
wire adjusting,sync,hadSync,main,check,adjustEnd,dummy,parity,
 on,off,visible,odd,ra0,active,first,hs;
wire [3:0] syncCount,hcount;
assign {h,row,raster,line,adjusting,adjust,lineAddress,nextLineAddress,ma,
 sync,syncCount,hadSync,main,check,adjustEnd,dummy,parity,count,on,off,
 visible,odd,ra0,active,first,hs,hcount}=crtcState;
wire iv=r8[1:0]==3;
wire [9:0] outputLine=iv?((line<<1)|odd):line;
// Register 14 supplies the high byte, register 15 the low byte.
wire [13:0] cursorPosition={regs[117:112],regs[127:120]};
wire starts=ma==cursorPosition && on && !off && active && h<r1;
wire [2:0] selected=starts?(3'd3-{1'b0,r8[7:6]}):cursorIndex;
wire segment=selected==3?control[7]:selected==4?control[6]:
 (selected==5 || selected==6)?control[5]:1'b0;
function [7:0] mask;
 input [4:0] registerIndex;
 begin
  case(registerIndex)
   4,6,7,10:mask=127;
   5,9,11:mask=31;
   8:mask=243;
   12,14:mask=63;
   default:mask=255;
  endcase
 end
endfunction
integer i;
always @(posedge clk or posedge reset)begin
 if(reset)begin
  index<=0;master<=16;divider<=0;control<=0;regs<=0;lightPen<=0;
  palette<=0;cellHorizontal<=0;cellLine<=0;cellDisplay<=0;
  cellCursor<=0;cursorIndex<=0;
 end else begin
  if(MULTIPLIER==3)divider<=divider==2?0:divider+2'd1;
  if(clock32)master<=master+5'd1;
  if(crtcWrite)begin
   if(!address)index<=data[4:0];
   else for(i=0;i<16;i=i+1)if(index==i)regs[8*i+:8]<=data&mask(i);
  end
  if(lightPenPulse)lightPen<=busAddress;
  if(ulaWrite)begin
   if(address)palette[4*data[7:4]+:4]<=data[3:0];
   else control<=data;
  end
  if(fetch)begin
   cellHorizontal<=h;cellLine<=outputLine;
   cellDisplay<=h>=skew && h<((r1+skew)&8'hff) && active &&
    !raster[3] && skew!=3 && !control[1];
   cellCursor<=visible && segment;
   if(selected!=0)cursorIndex<=(selected+3'd1)%7;
   else if(starts)cursorIndex<=0;
  end
 end
end
wire [4:0] rotatedMaster=master+5'd16;
wire [3:0] pixel=control[4]?{1'b0,master[3:1]}:rotatedMaster[4:1];
integer offset,j;
reg [7:0] shifted;
reg [3:0] colourIndex,colour;
reg [2:0] bitmapRgb;
always @* begin
 offset=pixel>>(3-control[3:2]);
 shifted=memoryData;
 for(j=0;j<16;j=j+1)if(j<offset)shifted={shifted[6:0],1'b1};
 colourIndex={shifted[7],shifted[5],shifted[3],shifted[1]};
 colour=palette[4*colourIndex+:4];
 bitmapRgb=colour[3]&&control[0]?colour[2:0]:colour[2:0]^3'b111;
end
wire [2:0] rgb=(cellDisplay?bitmapRgb:3'b0) ^ (cellCursor?3'b111:3'b0);
wire [7:0] column=cellHorizontal-skew;
wire [10:0] x=({3'b0,column}<<(control[4]?3:4))+pixel;
reg [3:0] subtract;
always @* case(scroll)
 0:subtract=8;1:subtract=4;2:subtract=10;3:subtract=5;
endcase
wire [3:0] high=ma[12]?ma[11:8]-subtract:ma[11:8];
wire [2:0] rasterPins={raster[2:1],ra0};
wire [14:0] linear=(ma[11]?15'h7c00:15'h3c00)|{5'b0,ma[9:0]};
wire [14:0] memoryAddress=ma[13]?
 (linear^((control[4]&&!control[1]&&!master[4])?15'h40:15'b0)):
 {high,ma[7:0],rasterPins};
wire [7:0] result=address && index>=12 && index<18?registers[8*index+:8]:8'b0;
assign proofState={crtcState,index,control,regs,lightPen,palette,master,
 divider,cellHorizontal,cellLine,cellDisplay,cursorIndex,cellCursor};
// TT-dependent pixel/render/position ports are excluded while TT is selected;
// timing, bus and bitmap pipeline state still advance on those histories.
assign observations={result,registers,palette,control,memoryAddress,fetch,
 (control[1]?1'b0:clock32&&master[0]),
 (control[1]?1'b0:cellDisplay||cellCursor),
 (control[1]?3'b0:rgb),(control[1]?11'b0:x),
 (control[1]?10'b0:cellLine),hs,sync,busSync,frame};
endmodule
