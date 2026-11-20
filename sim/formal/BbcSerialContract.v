// Independent elapsed-time contract for the closed digital serial fixture.
// CPU commands complete after the scheduler tick at the same bus endpoint.
module SerialContract(
 input clk, reset, tick, aciaRead, aciaWrite, serialRead, serialWrite, address,
 input [7:0] data, output equivalent);
 wire [51:0] state;
 wire [7:0] result;
 wire irq;
 BbcSerial actual(.clk(clk),.reset(reset),.io_tick(tick),
 .io_aciaRead(aciaRead),.io_aciaWrite(aciaWrite),.io_serialRead(serialRead),
 .io_serialWrite(serialWrite),.io_address(address),.io_data(data),
 .io_result(result),.io_irq(irq),.proofState(state));

 function [18:0] byteDuration;
 input [7:0] command;
 input [2:0] selector;
 reg ten;
 begin
  ten=command[4:2]==2 || command[4:2]==3 || command[4:2]==5;
  // Integer division specifies the original 2 MHz scheduler rounding.
  case(selector)
   0: byteDuration=ten ? 20000000/19200 : 22000000/19200;
   1: byteDuration=ten ? 20000000/9600 : 22000000/9600;
   2: byteDuration=ten ? 20000000/4800 : 22000000/4800;
   3: byteDuration=ten ? 20000000/2400 : 22000000/2400;
   4: byteDuration=ten ? 20000000/1200 : 22000000/1200;
   5: byteDuration=ten ? 20000000/300 : 22000000/300;
   6: byteDuration=ten ? 20000000/150 : 22000000/150;
   7: byteDuration=ten ? 20000000/75 : 22000000/75;
  endcase
 end
 endfunction
 reg [7:0] command=0;
 reg [2:0] selector=0;
 reg rs=0, empty=1, pending=0;
 reg [18:0] elapsed=0, duration=0;
 wire [18:0] period=byteDuration(command,selector);
 wire controlWrite=aciaWrite && !address;
 wire masterReset=controlWrite && data[1:0]==3;
 wire dataWrite=aciaWrite && address;
 wire serialTouch=serialRead || serialWrite;
 wire [7:0] serialValue=serialRead ? 8'hfe : data;
 wire finishing=tick && pending && elapsed+19'd1==duration;
 wire visibleEmpty=empty || finishing;
 wire [7:0] nextCommand=controlWrite && !masterReset ? data : command;
 wire nextRs=serialTouch ? serialValue[6] : rs;
 wire nextEmpty=dataWrite ? 1'b0 : visibleEmpty || masterReset;
 wire expectedIrq=!nextRs && nextEmpty && nextCommand[6:5]==1;
 wire txIrq=!rs && visibleEmpty && command[6:5]==1;
 wire [7:0] expectedResult=address ? 8'h00 : {txIrq,3'b000,rs,1'b0,visibleEmpty && !rs,1'b0};
 wire [18:0] remaining=duration-elapsed;
 initial assume(reset);
 // The real single CPU bus cannot issue ACIA and serial ULA writes together.
 // Both exclusions are discharged by the actual parent decoder proof.
 always @* begin
  assume(!(aciaWrite && serialTouch));
  assume(!(serialRead && serialWrite));
 end
 always @(posedge clk or posedge reset)
 if(reset) begin
  command<=0;selector<=0;rs<=0;empty<=1;pending<=0;elapsed<=0;duration<=0;
 end else begin
  if(tick && pending) begin
   elapsed<=elapsed+1;
   if(finishing) begin pending<=0;empty<=1;end
  end
  if(serialTouch) begin selector<=serialValue[2:0];rs<=serialValue[6];end
  if(controlWrite) begin
   if(masterReset) empty<=1;
   else command<=data;
  end
  if(dataWrite) begin empty<=0;pending<=1;elapsed<=0;duration<=period;end
 end
 assign equivalent=
  state=={command,selector,rs,empty,pending,remaining,period}
  && result==expectedResult && irq==expectedIrq
  && elapsed<=duration && (!pending || elapsed<duration);
endmodule
