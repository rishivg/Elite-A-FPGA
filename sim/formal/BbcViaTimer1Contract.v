// Independent elapsed-interval contract for the declared cold-reset T1 profile.
module ViaTimer1Contract(input clk,reset,tick,read,write,
 input [3:0] address,input [7:0] data,
 input phi2,ca1,ca2,ca2AfterWrite,cb1,cb1Pulse,cb2,
 input [7:0] portA,portB,output equivalent);
 wire [44:0] state;
 wire [15:0] busCount;
 wire [7:0] result;
 Via6522 actual(.clk(clk),.reset(reset),.io_tick(tick),.io_phi2(phi2),
 .io_read(read),.io_write(write),.io_address(address),.io_data(data),
 .io_portAIn(portA),.io_portBIn(portB),.io_ca1(ca1),.io_ca2(ca2),
 .io_ca2AfterWrite(ca2AfterWrite),.io_cb1(cb1),.io_cb1Pulse(cb1Pulse),.io_cb2(cb2),
 .proofTimerState(state),.proofTimerBus(busCount),.io_result(result));
 reg [7:0] low=8'hff,high=8'hff,mode=0;
 reg [15:0] span=16'hffff;
 reg [16:0] elapsed=1;
 reg running=0,square=1,flag=0;
 wire [16:0] endInterval={1'b0,span}+17'd2;
 wire boundary=tick && elapsed==endInterval-1;
 wire firing=boundary && running;
 wire loading=write && address==5;
 wire acknowledge=(read && address==4) ||
  (write && (address==5 || address==7 || (address==13 && data[6])));
 initial assume(reset);
 always @(posedge clk or posedge reset)
 if(reset) begin
  low<=8'hff;high<=8'hff;mode<=0;span<=16'hffff;elapsed<=1;
  running<=0;square<=1;flag<=0;
 end else begin
  if(tick) begin
   if(elapsed==endInterval) begin elapsed<=1;span<={high,low};end
   else elapsed<=elapsed+1;
  end
  if(firing) square<=!square;
  if(boundary && (!mode[6] || (write && address==11 && !data[6]))) running<=0;
  if(write && (address==4 || address==6)) low<=data;
  if(write && (address==5 || address==7)) high<=data;
  if(loading) begin span<={data,low};elapsed<=0;running<=1;square<=0;end
  if(write && address==11) mode<=data;
  flag<=firing || (flag && !acknowledge);
 end
 wire [15:0] visible={1'b0,span}+17'd1-elapsed;
 wire [15:0] counter=elapsed==0 ? span : visible;
 wire relatch=elapsed==endInterval;
 wire delayed=elapsed==0;
 wire [15:0] expectedBus=tick ?
   (delayed ? span : relatch ? {high,low} : visible-16'd1) : visible;
 wire [44:0] expectedState={counter,high,low,running,relatch,delayed,square,mode,flag};
 wire timerRead=read && address>=4 && address<=7;
 wire [7:0] expectedByte=address==4 ? expectedBus[7:0] :
  address==5 ? expectedBus[15:8] : address==6 ? low : high;
 assign equivalent=reset ||
  (elapsed<=endInterval && state==expectedState && busCount==expectedBus &&
   (!timerRead || result==expectedByte));
endmodule
