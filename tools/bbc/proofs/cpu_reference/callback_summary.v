// Base-edge collector model. Acknowledgement masks the following base edge;
// a callback during that edge stays latched until physical sampling resumes.
module CallbackSummary(input clk,output equivalent);
(* anyconst *) reg [255:0] initialState;
(* anyseq *) reg accepted,peripheralEvent,acknowledge,commandEvent,abortEvent,resetRequest;
reg first=1,eligible=1,previousAccepted=0,gap=0,resetPending=0;
reg level=0,previous=0,pending=0;
reg [255:0] sourceState=0;
wire ack=accepted && acknowledge;
wire resetAccess=accepted && resetRequest;
wire commandNmi=accepted && commandEvent;
// Actual reset establishes quiet state; the parent forbids another access on
// the following base edge. Only that first quiet edge is needed here.
wire pre=level || (peripheralEvent && !resetPending);
wire post=(pre && !ack && !resetAccess) || commandNmi;
wire pin=(pre || commandNmi) && !gap;
// The actual parent supplies at least two intervening base edges (in fact 15).
wire edgeEligible=eligible && !(ack && commandNmi) && !(resetAccess && (ack || commandNmi)) && !(previousAccepted && accepted);
wire retrigger=abortEvent && level;
wire delivered=retrigger;
wire rise=(pin && !previous) || delivered;
wire [255:0] abortSample,preSample,postSample;
M6502NmiSample beforeAbort(.state(sourceState),.nmi(retrigger ? 1'b0:pre),.sampled(abortSample));
M6502NmiSample beforeAccess(.state(abortSample),.nmi(pre),.sampled(preSample));
M6502NmiSample afterAccess(.state(preSample),.nmi(post),.sampled(postSample));
wire [255:0] markerMask=256'd1 << 56;
wire [255:0] observationMask=markerMask | (256'd1 << 131);
wire [255:0] expected=(initialState & ~observationMask) |
    ({255'd0,level} << 131) | ({255'd0,(pending || (level && !previous))} << 56);
wire invariant=sourceState==expected && gap==(previousAccepted && previous && !level && !resetPending) &&
    (!previous || level || gap || resetPending) && (!resetPending || (!level && !gap && previousAccepted));
wire windowEquivalent=(!accepted || postSample[56]==(pending || rise)) && postSample[131]==post &&
    (postSample & ~observationMask)==(initialState & ~observationMask);
wire observation=first || !edgeEligible || windowEquivalent;
assign equivalent=observation && (first || !edgeEligible || invariant);
always @(posedge clk)begin
 first<=0;
 if(first)begin
  sourceState<=initialState & ~markerMask;
  level<=initialState[131];previous<=initialState[131];pending<=0;
  gap<=0;eligible<=1;previousAccepted<=0;resetPending<=0;
 end else begin
  sourceState<=accepted ? (postSample & ~markerMask):postSample;
  pending<=accepted ? 1'b0:(pending || rise);
  level<=post;previous<=pin;
  gap<=ack && pin;
  resetPending<=resetAccess;
  eligible<=edgeEligible;previousAccepted<=accepted;
 end
end
endmodule
