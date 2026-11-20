module BbcAdcViaProbe(
    input wire clk, reset, tick, phi2, adc_read, adc_write, via_write, via_read,
    input wire [1:0] adc_address,
    input wire [3:0] via_address,
    input wire [7:0] data,
    output wire [7:0] adc_result, via_result,
    output wire irq, eoc
);
    wire physical_eoc, eoc_pulse;
    Adc7002 adc(.clk(clk), .reset(reset), .io_tick(tick),
        .io_read(adc_read), .io_write(adc_write), .io_address(adc_address), .io_data(data),
        .io_channels(64'h3330222011100000), .io_result(adc_result),
        .io_eocN(physical_eoc), .io_eocPulse(eoc_pulse), .io_eocBusN(eoc));
    Via6522 via(.clk(clk), .reset(reset), .io_tick(tick), .io_phi2(phi2),
        .io_read(via_read), .io_write(via_write), .io_address(via_address), .io_data(data),
        .io_portAIn(8'hff), .io_portBIn(8'hff),
        .io_ca1(1'b0), .io_ca2(1'b0), .io_ca2AfterWrite(1'b0), .io_cb1(eoc), .io_cb1Pulse(eoc_pulse), .io_cb2(1'b0),
        .io_result(via_result), .io_irq(irq), .io_portA(), .io_portB(), .io_driveA());
endmodule
