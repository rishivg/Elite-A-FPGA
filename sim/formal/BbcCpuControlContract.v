// Documented NMOS stack/jump bus and architectural retirement contract.
module BbcCpuControlContract(input clk, output equivalent, output [7:0] opcode);
// Preserve the entire symbolic opcode for non-vacuous domain admission.
(* anyconst *) reg [7:0] symbolicOpcode;
assign opcode=symbolicOpcode;
(* anyconst *) reg [7:0] a, x, y, s, p, discarded, operand;
(* anyconst *) reg [7:0] writeRead0, writeRead1, writeRead2;
(* anyconst *) reg [15:0] base,vector;
wire isPush=opcode==8'h08 || opcode==8'h48;
wire isPull=opcode==8'h28 || opcode==8'h68;
wire isCall=opcode==8'h20;
wire isRts=opcode==8'h60;
wire isRti=opcode==8'h40;
wire isIndirect=opcode==8'h6c;
wire isJump=opcode==8'h4c;
wire [3:0] duration=isPush || isJump ? 3 : isPull ? 4 : isIndirect ? 5 : 6;
wire [15:0] returnPc=base+16'd15;
wire [15:0] pointer={discarded,operand};
always @* assume(opcode==8'h08 || opcode==8'h48 || opcode==8'h28 || opcode==8'h68 || opcode==8'h4c || opcode==8'h6c || opcode==8'h20 || opcode==8'h60 || opcode==8'h40);
wire [5:0] step;
wire reset,busMatches,prefixReady,writing,instruction;
wire [15:0] address,pc;
wire [7:0] value,accumulator,indexX,indexY,stack,flags,retiredFlags;
reg [15:0] expectedAddress;
reg [7:0] returnedData;
always @* begin
 expectedAddress=0;returnedData=8'hea;
 case(step)
  26: begin expectedAddress=base+13;returnedData=opcode;end
 27: begin expectedAddress=base+14;returnedData=isCall || isJump ? vector[7:0] : isIndirect ? operand : discarded;end
 28: begin expectedAddress=16'h0100|{8'd0,s};returnedData=writeRead0;end
 29: begin expectedAddress=16'h0100|{8'd0,(s+8'd1)};returnedData=isRts ? vector[7:0] : operand;end
 30: begin expectedAddress=16'h0100|{8'd0,(s+8'd2)};returnedData=isRti ? vector[7:0] : vector[15:8];end
 31: begin expectedAddress=16'h0100|{8'd0,(s+8'd3)};returnedData=vector[15:8];end
 endcase
 if(isJump && step==28)begin expectedAddress=base+15;returnedData=vector[15:8];end
 if(isIndirect && step==28)begin expectedAddress=base+15;returnedData=discarded;end
 if(isIndirect && step==29)begin expectedAddress=pointer;returnedData=vector[7:0];end
 if(isIndirect && step==30)begin expectedAddress={pointer[15:8],pointer[7:0]+8'd1};returnedData=vector[15:8];end
 if(isCall && step==29)begin expectedAddress=16'h0100|{8'd0,s};returnedData=writeRead1;end
 if(isCall && step==30)begin expectedAddress=16'h0100|{8'd0,(s-8'd1)};returnedData=writeRead2;end
 if(isCall && step==31)begin expectedAddress=base+15;returnedData=vector[15:8];end
 if(isRts && step==31)begin expectedAddress=vector;returnedData=8'hea;end
 if(step>=26+duration)returnedData=8'hea;
end
wire expectedWrite=step==15 || (isPush && step==28) || (isCall && (step==29 || step==30));
wire [7:0] expectedValue=step==15 ? p : isCall ? (step==29 ? returnPc[15:8] : returnPc[7:0]) : opcode==8'h08 ? p|8'h30 : a;
wire [15:0] endPc=isRts ? vector+16'd1 : isJump || isIndirect || isCall || isRti ? vector : base+16'd14;
wire [15:0] diagnosticPc=pc-16'd1;
wire [7:0] expectedS=isPush ? s-8'd1 : isPull ? s+8'd1 : isCall ? s-8'd2 : isRts ? s+8'd2 : isRti ? s+8'd3 : s;
wire [7:0] expectedP=opcode==8'h28 || isRti ? operand : opcode==8'h68 ? (p&8'h7d)|(operand[7] ? 8'h80 : 8'h00)|(operand==0 ? 8'h02 : 8'h00) : p;
wire [5:0] retirementStep=27+duration;
BbcCpuContractPrefix prefix(
 .clk(clk),.a(a),.x(x),.y(y),.s(s),.p(p),.opcode(opcode),.base(base),
 .targetAddress(expectedAddress),.targetRead(returnedData),
 .targetWrite(expectedWrite),.targetValue(expectedValue),.retirementStep(retirementStep),
 .step(step),.reset(reset),.busMatches(busMatches),.prefixReady(prefixReady),.writing(writing),.instruction(instruction),
 .address(address),.pc(pc),.value(value),.accumulator(accumulator),.indexX(indexX),.indexY(indexY),
 .stack(stack),.flags(flags),.retiredFlags(retiredFlags)
);
assign equivalent=reset || (prefixReady &&
 (step>=26+duration || busMatches) &&
 (step!=27+duration || (instruction && diagnosticPc==endPc)) &&
 (step!=28+duration || (accumulator==(opcode==8'h68 ? operand : a) && indexX==x && indexY==y && stack==expectedS && retiredFlags==(expectedP&8'hcf))));
endmodule
