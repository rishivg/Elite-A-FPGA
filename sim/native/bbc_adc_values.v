// Expose only the current generated ADC's public pins to the result probe.
module BbcAdcValues(
    input wire clk, reset, tick, write,
    input wire [1:0] address,
    input wire [7:0] data,
    input wire [63:0] channels,
    output wire [7:0] result,
    output wire eoc
);
wire pulse, busEoc;
Adc7002 adc(
    .clk(clk), .reset(reset), .io_tick(tick), .io_write(write),
    .io_read(1'b0), .io_address(address), .io_data(data), .io_channels(channels),
    .io_result(result), .io_eocN(eoc), .io_eocPulse(pulse), .io_eocBusN(busEoc)
);
endmodule
