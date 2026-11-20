// Actual generated BBC components under declared external bus stimulus.
module BbcSoundBusTest #(parameter MASTER_MULTIPLIER = 1) (
    input wire clk, reset,
    input wire io_enabled, io_touch,
    input wire [7:0] io_data,
    output wire io_write,
    output wire [7:0] io_writeData,
    output wire [7:0] io_sample,
    output wire [3:0] io_pins
);
    reg [1:0] divider = 0;
    always @(posedge clk or posedge reset)
        if (reset) divider <= 0;
        else divider <= divider == 2 ? 0 : divider + 1;
    wire clock32 = MASTER_MULTIPLIER == 1 || divider == 2;
    BbcAudio audio(.clk(clk), .reset(reset), .io_clock32(clock32), .io_enabled(io_enabled),
        .io_touch(io_touch), .io_data(io_data), .io_write(io_write), .io_acceptedData(io_writeData),
        .io_sample(io_sample), .io_pins(io_pins), .io_pdm());
endmodule
