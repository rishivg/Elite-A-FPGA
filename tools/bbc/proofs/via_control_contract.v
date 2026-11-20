// Scheduler callbacks precede pin input changes and accepted bus writes.
// See pinned jsbeeb Via.setca1/setcb1, PCR/ORA/ORB writes and PulseWidthCycles.
module ViaControlContract(input clk, output equivalent);
(* anyseq *) reg reset,tick,write,ca1,cb1,cb1Pulse;
(* anyseq *) reg [3:0] address;
(* anyseq *) reg [7:0] data;
wire [13:0] state;
wire [7:0] pcr;
wire a,b,aOutput,bOutput,aRise,bRise,aFall,bFall;
ViaControlPins actual(.clk(clk),.reset(reset),.io_tick(tick),.io_write(write),
 .io_address(address),.io_data(data),.io_ca1(ca1),.io_cb1(cb1),.io_cb1Pulse(cb1Pulse),
 .io_pcr(pcr),.io_ca2(a),.io_cb2(b),.io_ca2Output(aOutput),.io_cb2Output(bOutput),
 .io_ca2Rise(aRise),.io_cb2Rise(bRise),.io_ca2Fall(aFall),.io_cb2Fall(bFall),
 .proofState(state));
reg [7:0] control=0;
reg levelA=0,levelB=0,scheduledA=0,scheduledB=0,oldA=0,oldB=0;
wire edgeA=ca1!=oldA && ca1==control[0];
wire edgeB=(cb1!=oldB && cb1==control[4]) || cb1Pulse;
wire callbackA=(tick && scheduledA) || (edgeA && (control&8'h0c)==8'h08);
wire callbackB=(tick && scheduledB) || (edgeB && (control&8'hc0)==8'h80);
wire schedulerA=callbackA ? 1'b1 : levelA;
wire schedulerB=callbackB ? 1'b1 : levelB;
wire pcrWrite=write && address==12;
wire [7:0] newControl=pcrWrite ? data : control;
wire busA=write && address==1 && ((control&8'h0e)==8'h08 || (control&8'h0e)==8'h0a);
wire busB=write && address==0 && ((control&8'he0)==8'h80 || (control&8'he0)==8'ha0);
wire newA=busA ? 1'b0 : pcrWrite && data[3] ? ((data&8'h0e)!=8'h0c) : schedulerA;
wire newB=busB ? 1'b0 : pcrWrite && data[7] ? ((data&8'he0)!=8'hc0) : schedulerB;
wire riseA=(!levelA && schedulerA && control[3]) || (!schedulerA && newA && newControl[3]);
wire riseB=(!levelB && schedulerB && control[7]) || (!schedulerB && newB && newControl[7]);
wire fallA=schedulerA && !newA && newControl[3];
wire fallB=schedulerB && !newB && newControl[7];
always @(posedge clk or posedge reset) begin
 if(reset) begin
  control<=0; levelA<=0; levelB<=0; scheduledA<=0; scheduledB<=0; oldA<=0; oldB<=0;
 end else begin
  control<=newControl; levelA<=newA; levelB<=newB; oldA<=ca1; oldB<=cb1;
  if(tick) begin
   scheduledA<=write && address==1 && (control&8'h0e)==8'h0a;
   scheduledB<=write && address==0 && (control&8'he0)==8'ha0;
  end
 end
end
assign equivalent=state=={control,levelA,levelB,scheduledA,scheduledB,oldA,oldB} &&
 {pcr,a,b,aOutput,bOutput,aRise,bRise,aFall,bFall}==
 {control,levelA,levelB,control[3],control[7],riseA,riseB,fallA,fallB};
endmodule
