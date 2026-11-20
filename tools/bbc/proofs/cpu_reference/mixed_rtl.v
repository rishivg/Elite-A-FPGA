// Compare each engine's own bus request after the normal startup prefix.
module MixedDirect(input clk, output equivalent, output faultDefined);
    (* anyseq *) reg irqInput, nmiInput;
    (* anyseq *) reg [7:0] data;
    (* anyseq *) reg [1:0] opcodeChoice;
    reg fresh = 1;
    reg [3:0] phase = 0;
    always @(posedge clk) begin
        fresh <= 0;
        if (!fresh && phase < 11) phase <= phase + 1;
    end

    // The parent startup contract establishes this quiet prefix separately.
    wire irq = phase == 11 && irqInput;
    wire nmi = phase == 11 && nmiInput;
    reg [255:0] state = 256'hINITIAL_STATE;
    reg [63:0] pins = 64'hINITIAL_PINS;
    wire [7:0] opcode = opcodeChoice == 0 ? 8'h58 :
                        opcodeChoice == 1 ? 8'h78 : 8'hea;
    wire [7:0] requested = phase == 7 ? 8'ha9 : phase == 8 ? 8'h40 :
                           phase == 9 ? 8'h8d : pins[25] ? opcode : data;
    reg [7:0] held = 0;
    wire [63:0] response = (pins & ~64'hcff0000) |
        ({56'd0, requested} << 16) | ({63'd0, irq} << 26) | ({63'd0, nmi} << 27);
    wire [255:0] following;
    wire [63:0] nextPins;
    wire trap;
    M6502ReferenceNext source(
        .state_in(state), .pins_in(response), .state_out(following),
        .pins_out(nextPins), .trap(trap)
    );
    always @(posedge clk) if (!fresh) begin
        state <= following;
        pins <= nextPins;
        held <= requested;
    end

    wire [15:0] address;
    wire [7:0] value;
    wire writing, sync;
    BbcCpu dut(
        .clk(clk), .reset(fresh), .enable(!fresh), .irq(irq), .nmi(nmi),
        .warm_reset(1'b0), .read_data(held), .address(address),
        .write_data(value), .write_enable(writing), .sync(sync)
    );
    assign equivalent = fresh || (!trap && address == pins[15:0] &&
        writing == !pins[24] && sync == pins[25] && (!writing || value == pins[23:16]));
    reg definedHistory = 1;
    always @(posedge clk) if (!fresh) definedHistory <= definedHistory && !trap;
    assign faultDefined = !definedHistory || trap || equivalent;
endmodule
