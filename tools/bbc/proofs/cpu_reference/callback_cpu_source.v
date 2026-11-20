// Complete maintained C transition; only the stored/raw NMI pin may differ.
// The independently collected event marker is ORed into the existing pipeline.
module SourcePinNormalization(input[255:0] state,input[63:0] pins,input pinA,pinB,event,output equivalent,observation);
wire[255:0] withEvent=state | (event ? (256'd1<<56):256'd0);
wire[255:0] beforeA=(withEvent & ~(256'd1<<131)) | ({255'd0,pinA}<<131);
wire[255:0] beforeB=(withEvent & ~(256'd1<<131)) | ({255'd0,pinB}<<131);
wire[63:0] pinsA=(pins & ~64'h8000000) | ({63'd0,pinA}<<27);
wire[63:0] pinsB=(pins & ~64'h8000000) | ({63'd0,pinB}<<27);
wire[255:0] nextA,nextB;
wire[63:0] outA,outB;
wire trapA,trapB;
M6502ReferenceNext left(.state_in(beforeA),.pins_in(pinsA),.state_out(nextA),.pins_out(outA),.trap(trapA));
M6502ReferenceNext right(.state_in(beforeB),.pins_in(pinsB),.state_out(nextB),.pins_out(outB),.trap(trapB));
assign observation=trapA || trapB || (
 ((nextA^nextB)&~(256'd1<<131))==0 && ((outA^outB)&~64'h8000000)==0 &&
 nextA[131]==pinA && nextB[131]==pinB && outA[27]==pinA && outB[27]==pinB);
assign equivalent=(trapA==trapB) && observation;
endmodule
