// Independent elapsed-up byte counters and clocked PB6 sampling.
// Only the initial declared reset is assumed; subsequent inputs are arbitrary.
module ViaTimer2Contract(input clk,reset,tick,read,write,phi2,
 input [3:0] address,input [7:0] data,
 input ca1,ca2,ca2AfterWrite,cb1,cb1Pulse,cb2,
 input [7:0] portA,portB,output equivalent);
 wire [49:0] state;
 wire underflow;
 wire [15:0] busCount;
 wire [7:0] result;
 Via6522 actual(.clk(clk),.reset(reset),.io_tick(tick),.io_phi2(phi2),
 .io_read(read),.io_write(write),.io_address(address),.io_data(data),
 .io_portAIn(portA),.io_portBIn(portB),.io_ca1(ca1),.io_ca2(ca2),
 .io_ca2AfterWrite(ca2AfterWrite),.io_cb1(cb1),.io_cb1Pulse(cb1Pulse),.io_cb2(cb2),
 .proofTimerState(state),.proofTimerBus(busCount),.io_result(result),.proofUnderflow(underflow));
 reg [7:0] low=8'hff,high=8'hff,mode=0;
 reg [7:0] originLow=8'hff,originHigh=8'hff,elapsedLow=0,elapsedHigh=0;
 reg running=0,delayed=0,reloading=0,flag=0;
 reg [1:0] sampled=3;
 reg pulseQueued=0,wasLow=1,underflowQueued=0,loadPending=0;
 wire [7:0] lowCount=originLow-elapsedLow;
 wire [7:0] highCount=originHigh-elapsedHigh;
 wire [15:0] counter={highCount,lowCount};
 wire shifting=mode[4:2]==4 || mode[3:2]==1;
 wire nextShifting=data[4:2]==4 || data[3:2]==1;
 wire phaseFalls=!wasLow && !phi2;
 wire counterEdge=mode[5] ? phaseFalls && pulseQueued && !loadPending : tick;
 wire reloadEdge=mode[5] ? phaseFalls : tick;
 wire lowWrap=counterEdge && !delayed && !reloading && lowCount==0;
 wire counting=counterEdge && !delayed && !(shifting && reloading);
 wire firing=counting && counter==0 && running;
 wire acknowledge=(read && address==8) ||
  (write && (address==9 || (address==13 && data[5])));
 initial assume(reset);
 always @(posedge clk or posedge reset)
 if(reset) begin
  low<=8'hff;high<=8'hff;mode<=0;originLow<=8'hff;originHigh<=8'hff;
  elapsedLow<=0;elapsedHigh<=0;running<=0;delayed<=0;reloading<=0;flag<=0;
  sampled<=3;pulseQueued<=0;wasLow<=1;underflowQueued<=0;loadPending<=0;
 end else begin
  wasLow<=!phi2;
  if(phaseFalls) loadPending<=0;
  if(!phi2) sampled[0]<=portB[6];
  if(tick) begin sampled[1]<=sampled[0];pulseQueued<=sampled[1] && !sampled[0];underflowQueued<=0;end
  if(mode[5] && lowWrap) underflowQueued<=1;
  if(shifting && reloading && reloadEdge) begin originLow<=low;elapsedLow<=0;reloading<=0;end
  if(counterEdge) begin
   if(delayed) delayed<=0;
   else if(shifting && reloading) begin originLow<=low;elapsedLow<=0;reloading<=0;end
   else begin
    elapsedLow<=elapsedLow+1;
    if(lowCount==0) begin elapsedHigh<=elapsedHigh+1;if(shifting) reloading<=1;end
   end
  end
  if(firing) running<=0;
  if(write && address==8) low<=data;
  if(write && address==9) begin
   high<=data;originLow<=low;originHigh<=data;elapsedLow<=0;elapsedHigh<=0;
   running<=1;delayed<=!mode[5];reloading<=0;underflowQueued<=0;loadPending<=mode[5] && phi2;
  end
  if(write && address==11) begin mode<=data;if(!nextShifting) reloading<=0;end
  flag<=firing || (flag && !acknowledge);
 end
 wire [15:0] visible=shifting ? {highCount,lowCount+delayed} : counter+delayed;
 wire [15:0] expectedBus=counterEdge ? (delayed ? counter :
   shifting && reloading ? {highCount,low} : counter-16'd1) : visible;
 wire [49:0] expectedState={counter,high,low,running,delayed,reloading,underflowQueued,mode,flag,sampled[0],sampled[1],pulseQueued,!wasLow,loadPending};
 wire timerRead=read && (address==8 || address==9);
 wire [7:0] expectedByte=address==8 ? expectedBus[7:0] : expectedBus[15:8];
 assign equivalent=reset || (!(delayed && reloading) && (shifting || !reloading) && state==expectedState &&
   busCount==expectedBus && underflow==(lowWrap || underflowQueued) &&
   (!timerRead || result==expectedByte));
endmodule
