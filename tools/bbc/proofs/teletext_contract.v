// Imperative character/pin semantics, independently following public
// Teletext.advance/handleControlCode and the three input pin methods.
module TeletextHistoryReference(input clk,reset,enable,input [2:0] operation,
 input [7:0] data,output [76:0] state,output [51:0] outputs,
 output invariant);
reg [6:0] q0,q1,q2,held,character;
reg [2:0] colour,bg,fg,cellBg;
reg sep,dbl,second,was,gfx,conceal,flash,hold,dew,lose,ra0,
 cellDbl,cellConceal,cellFlash;
reg [1:0] heldSet,nextSet,cellSet;
reg [3:0] scan;
reg [5:0] flashCount;
reg [2:0] nc,nb;
reg ns,nd,ng,nconceal,nflash,nh;
reg [1:0] nglyph;
reg [6:0] rendered;
reg [1:0] renderedSet;
reg useHeld;
always @* begin
 nc=colour;nb=bg;ns=sep;nd=dbl;ng=gfx;nconceal=conceal;
 nflash=flash;nh=hold;nglyph=nextSet;
 if(q0<32)begin
  case(q0)
   1,2,3,4,5,6,7:begin ng=0;nc=q0[2:0];nconceal=0;nglyph=0;end
   17,18,19,20,21,22,23:begin ng=1;nc=q0[2:0];nconceal=0;nglyph=sep?2:1;end
   8:nflash=1;
   9:nflash=0;
   12:nd=0;
   13:nd=1;
   24:nconceal=1;
   25:begin ns=0;nglyph=gfx?1:0;end
   26:begin ns=1;nglyph=gfx?2:0;end
   28:nb=0;
   29:nb=colour;
   30:nh=1;
   31:nh=0;
  endcase
 end
 useHeld=gfx && (hold||nh) && nd==dbl;
 rendered=q0;renderedSet=nextSet;
 if(q0<32)begin
  rendered=32;
  if(useHeld)begin rendered=held;renderedSet=heldSet;end
 end
end
always @(posedge clk or posedge reset)begin
 if(reset)begin
  q0<=0;q1<=0;q2<=0;colour<=7;bg<=0;sep<=0;dbl<=0;second<=0;
  was<=0;gfx<=0;conceal<=0;flash<=0;hold<=0;held<=0;
  heldSet<=0;nextSet<=0;scan<=0;flashCount<=0;dew<=0;lose<=0;
  ra0<=0;character<=32;cellSet<=0;fg<=0;cellBg<=0;
  cellDbl<=0;cellConceal<=0;cellFlash<=0;
 end else if(enable)begin
  case(operation)
   1:begin q0<=q1;q1<=q2;q2<=data[6:0];end
   2:begin
    dew<=data[0];
    if(dew && !data[0])begin scan<=0;second<=0;flashCount<=flashCount+6'd1;end
   end
   3:begin
    lose<=data[0];
    if(lose && !data[0])begin
     colour<=7;bg<=0;hold<=0;held<=32;nextSet<=0;heldSet<=0;
     flash<=0;sep<=0;gfx<=0;conceal<=0;dbl<=0;was<=0;
     if(scan==9)begin scan<=0;second<=second?0:was;end
     else scan<=scan+4'd1;
    end
   end
   4:ra0<=data[0];
   5:begin
    colour<=nc;bg<=nb;sep<=ns;dbl<=nd;gfx<=ng;conceal<=nconceal;
    flash<=nflash;hold<=nh;nextSet<=nglyph;
    if(q0<32)begin
     if(!useHeld)held<=32;
     if(q0==13)was<=1;
    end else if(gfx)begin
     if(q0[5])begin held<=q0;heldSet<=nextSet;end
    end else held<=32;
    character<=rendered;cellSet<=renderedSet;fg<=colour;cellBg<=nb;
    cellDbl<=dbl;cellConceal<=conceal||nconceal;cellFlash<=flash&&nflash;
   end
  endcase
 end
end
wire [4:0] raster=cellDbl?(scan+(second?5'd10:5'd0)):{scan,ra0};
wire suppressed=cellConceal || (cellFlash && flashCount<16) || (second && !dbl);
wire [30:0] attributes={flashCount,scan,held,hold,flash,conceal,gfx,was,
 second,dbl,sep,bg,colour};
assign state={q0,q1,q2,colour,bg,sep,dbl,second,was,gfx,conceal,flash,
 hold,held,heldSet,nextSet,scan,flashCount,dew,lose,ra0,character,
 cellSet,fg,cellBg,cellDbl,cellConceal,cellFlash};
assign outputs={character,cellSet,raster,fg,cellBg,suppressed,attributes};
// Public nextGlyphs is derived from current gfx/sep at legal transitions.
// This invariant, and the ten-row counter range, are proved from reset.
assign invariant=nextSet==(gfx?(sep?2:1):0) && scan<10 && heldSet<3 && cellSet<3;
endmodule
