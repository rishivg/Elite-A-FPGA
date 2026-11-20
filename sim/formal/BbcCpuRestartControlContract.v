module BbcCpuRestartControlContract(input clk,output equivalent);
(* anyseq *)reg reset,enable,request;
(* anyseq *)reg [7:0] data;
wire [15:0] address,pc,coreAddress,corePc;
wire writing,instruction,sync,beginReset,secondDummy,coreSync,coreWrite,coreInstruction;
BbcCpu actual(.clk(clk),.reset(reset),.enable(enable),.warm_reset(request),.irq(1'b0),.nmi(1'b0),.read_data(data),
 .address(address),.pc(pc),.write_enable(writing),.instruction(instruction),.sync(sync),
 .proofWarmBegin(beginReset),.proofWarmSecond(secondDummy),.proofCoreSync(coreSync),.proofCoreWrite(coreWrite),.proofCoreInstruction(coreInstruction),.proofCoreAddress(coreAddress),.proofCorePc(corePc));
reg [1:0] coldRemaining=2;
reg queued=0;
reg [1:0] phase=0;
reg [15:0] restartPc=0;
initial assume(reset);
wire eligible=coldRemaining==0 && phase==0 && coreSync && (queued||request);
always @(posedge clk)begin
 if(reset)begin coldRemaining<=2;queued<=0;phase<=0;end
 else begin
  if(enable && coldRemaining!=0)coldRemaining<=coldRemaining-1;
  if(request)queued<=1;
  if(enable)begin
   case(phase)
    0:if(eligible)begin queued<=0;phase<=1;restartPc<=coreAddress;end
    1:phase<=0;
   endcase
  end
 end
end
wire [15:0] expectedAddress=coldRemaining!=0?corePc:phase==1?restartPc:coreAddress;
assign equivalent=beginReset==eligible && secondDummy==(phase==1) && address==expectedAddress &&
 pc==corePc && writing==(coldRemaining==0 && phase==0 && coreWrite) &&
 instruction==(phase==0 && coreInstruction) && sync==(!reset && (coldRemaining==2 || (coldRemaining==0 && phase==0 && coreSync)));
endmodule
