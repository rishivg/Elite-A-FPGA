// NMI selection/acknowledgement and first-handler protocol properties.
// Observe the existing core context without constraining or injecting it.
// The only assumption is the initial declared reset. Pipeline predicates
// strengthen the first-handler obligation as jointly proved invariants.
module CpuInterruptContract(input clk,reset,enable,irq,nmi,input[7:0]data,output equivalent);
wire [15:0] address,vectorBus;
wire [5:0] state,polls;
wire [1:0] restartControls;
wire pending,res,previous,selected,accepted;
BbcCpu actual(.clk(clk),.reset(reset),.enable(enable),.irq(irq),.nmi(nmi),
 .warm_reset(1'b0),
 .read_data(data),.address(address),.proofState(state),.proofPending(pending),
 .proofResetFlag(res),.proofPreviousNmi(previous),.proofVector(selected),
 .proofPolls(polls),.proofVectorBus(vectorBus),.proofAccepted(accepted),
 .proofRestartControls(restartControls));
reg previousNmi=0,watch=0,vector=0,resetVector=0;
reg [1:0] vectorStage=0,firstHandler=0;
wire rise=nmi && !previousNmi;
wire select=accepted && state==9;
initial assume(reset);
always @(posedge clk) begin
 previousNmi<=nmi;
 if(reset) begin watch<=0;vector<=0;resetVector<=0;vectorStage<=0;firstHandler<=0;end
 else begin
  if(select && !res)watch<=0;
  else if(rise)watch<=1;
  if(select) begin vector<=!res && (watch || rise);resetVector<=res;vectorStage<=1;end
  else if(accepted) begin
   if(vectorStage==1)vectorStage<=2;
   else vectorStage<=0;
  end
  if(accepted && state==11)firstHandler<=3;
  else if(accepted && firstHandler!=0)firstHandler<=firstHandler-1;
 end
end
wire [15:0] chosenVector=resetVector ? 16'hfffc : vector ? 16'hfffa :16'hfffe;
assign equivalent=reset || (
 restartControls==0 &&
 previous==previousNmi && pending==watch && selected==vector &&
 (vectorStage!=1 || (state==10 && vectorBus==chosenVector)) &&
 (vectorStage!=2 || (state==11 && address==chosenVector)) &&
 (firstHandler!=3 || (state==22 && polls==0)) &&
 (firstHandler!=2 || (state==23 && (polls&6'h1b)==0)) &&
 (firstHandler!=1 || (state==12 && (polls&6'h09)==0)));
endmodule
