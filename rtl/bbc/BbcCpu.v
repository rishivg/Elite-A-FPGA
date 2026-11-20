// Independent NMOS CPU wrapper. Upstream core and its notices remain unchanged.
module BbcCpu(
    input wire clk, input wire reset, input wire enable,
    input wire irq, input wire nmi, input wire warm_reset,
    input wire [7:0] read_data,
    output wire [15:0] address,
    output wire [7:0] write_data,
    output wire write_enable,
    output wire instruction,
    output wire sync,
    output wire [15:0] pc,
    output wire [7:0] accumulator, index_x, index_y, stack_pointer, flags
);
    // Arlet starts reset at the stack phase. NMOS first makes two dummy PC
    // reads. Keep them visible on the bus while holding the internal core.
    reg [1:0] reset_dummy = 2;
    // A restart pulse may arrive between enables or during an instruction.
    // Finish that instruction, then expose two dummy reads at its next SYNC.
    reg warm_pending = 0, warm_second = 0;
    reg [15:0] warm_address = 0;
    wire warm_accept = reset_dummy == 0 && !warm_second && core_sync &&
                       (warm_pending || warm_reset);
    always @(posedge clk)
        if (reset) begin
            warm_pending <= 0;
            warm_second <= 0;
        end else begin
            if (warm_reset) warm_pending <= 1;
            if (enable && warm_accept) begin
                warm_pending <= 0;
                warm_second <= 1;
                warm_address <= core_address;
            end else if (enable && warm_second) warm_second <= 0;
        end
    always @(posedge clk)
        if (reset) reset_dummy <= 2;
        else if (enable && reset_dummy != 0) reset_dummy <= reset_dummy - 1;
    wire [15:0] core_address;
    wire [15:0] core_pc;
    wire core_write, core_sync, core_instruction;
    wire step = enable && reset_dummy == 0;
    bbc_cpu_ce core(.CE(step), .clk(clk), .reset(reset), .AB(core_address), .DI(read_data),
        .DO(write_data), .WE(core_write), .IRQ(irq), .NMI(nmi), .RDY(1'b1),
        .BBC_PC(core_pc), .BBC_FETCH(core_instruction), .BBC_A(accumulator),
        .BBC_X(index_x), .BBC_Y(index_y), .BBC_S(stack_pointer),
        .BBC_SYNC(core_sync), .BBC_WARM(warm_second), .BBC_WARM_BEGIN(warm_accept),
        .BBC_P(flags));
    assign address = reset_dummy != 0 ? core_pc : warm_second ? warm_address : core_address;
    assign write_enable = reset_dummy == 0 && !warm_second && core_write;
    assign instruction = core_instruction && !warm_second;
    assign sync = !reset && (reset_dummy == 2 || (reset_dummy == 0 && !warm_second && core_sync));
    assign pc = core_pc;
endmodule
