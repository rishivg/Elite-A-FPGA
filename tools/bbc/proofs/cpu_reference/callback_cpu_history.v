// Source event windows and both physical/base collectors over arbitrary history.
// The base clock is replaced by the independently checked parent's enable.
module PhysicalCallbackHistory(input clk,output equivalent,observation);
(* anyconst *) reg [255:0] initialState;
(* anyconst *) reg initialCallback;
(* anyseq *) reg callbackInput,acceptInput,acknowledge,commandEvent,abortEvent,resetRequest;
reg first=1;
reg [1:0] divider=0;
wire base=CALLBACK_BASE;
reg eligible=1,previousAccepted=0,gap=0,level=0,callbackHeld=0,resetPending=0;
reg previous=0,pending=0,basePrevious=0,basePending=0;
reg[255:0] sourceState=0;
wire accepted=base && acceptInput;
wire ack=accepted && acknowledge;
wire resetAccess=accepted && resetRequest;
wire commandNmi=accepted && commandEvent;
wire pre=level || (callbackHeld && !resetPending);
wire post=(pre && !ack && !resetAccess) || commandNmi;
wire heldPin=pre && !gap;
wire pin=(pre || commandNmi) && !gap;
wire retrigger=base && abortEvent && level;
wire delivered=retrigger;
wire rise=(pin && !previous) || delivered;
wire baseRise=(pin && !basePrevious) || delivered;
wire edgeEligible=eligible && !(ack && commandNmi) && !(resetAccess && (ack || commandNmi)) && !(previousAccepted && accepted);
wire[255:0] abortSample,preSample,postSample;
M6502NmiSample beforeAbort(.state(sourceState),.nmi(retrigger ? 1'b0:pre),.sampled(abortSample));
M6502NmiSample beforeAccess(.state(abortSample),.nmi(pre),.sampled(preSample));
M6502NmiSample afterAccess(.state(preSample),.nmi(post),.sampled(postSample));
wire[255:0] markerMask=256'd1<<56;
wire[255:0] observationMask=markerMask | (256'd1<<131);
wire[255:0] expected=(initialState & ~observationMask) |
 ({255'd0,level}<<131) | ({255'd0,(basePending || (level&&!basePrevious))}<<56);
wire sourceInvariant=sourceState==expected && gap==(previousAccepted && basePrevious && !level && !resetPending) &&
 (!basePrevious || level || gap || resetPending) && (!resetPending || (!level && !gap && previousAccepted));
wire physicalInvariant=pending==(basePending || (previous && !basePrevious)) &&
 (divider==0 ? previous==basePrevious:previous==heldPin) && divider<=2;
assign observation=first || !edgeEligible || !accepted || postSample[56]==(pending || rise);
assign equivalent=observation && (first || !edgeEligible || (sourceInvariant && physicalInvariant));
always @(posedge clk)begin
 first<=0;
 if(first)begin
  divider<=0;eligible<=1;previousAccepted<=0;gap<=0;level<=initialState[131];callbackHeld<=initialCallback;
  previous<=initialState[131];basePrevious<=initialState[131];pending<=0;basePending<=0;
  sourceState<=initialState & ~markerMask;resetPending<=0;
 end else begin
  divider<=base ? 0:divider+1;
  previous<=pin;pending<=accepted ? 0:(pending || rise);
  eligible<=edgeEligible;
  if(base)begin
   sourceState<=accepted ? (postSample & ~markerMask):postSample;
   basePending<=accepted ? 0:(basePending || baseRise);
   basePrevious<=pin;
   level<=post;gap<=ack && pin;callbackHeld<=callbackInput;
   resetPending<=resetAccess;
   previousAccepted<=accepted;
  end
 end
end
endmodule
