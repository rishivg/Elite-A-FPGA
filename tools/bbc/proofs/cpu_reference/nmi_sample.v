// Packed form of reference/nmi.h; widths follow frontend.FIELDS.
module M6502NmiSample(
    input [255:0] state,
    input nmi,
    output [255:0] sampled
);
    wire [63:0] storedPins = (state[167:104] & ~64'h8000000) |
                            ({63'd0, nmi} << 27);
    wire [31:0] pipeline = state[79:48] |
                          ((nmi && !state[131]) ? 32'h100 : 32'd0);
    assign sampled = {state[255:168], storedPins, state[103:80], pipeline, state[47:0]};
endmodule
