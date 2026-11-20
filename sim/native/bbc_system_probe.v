// The production VIA, IC32/keyboard wiring and stable-window sound frontend.
module BbcSystemProbe(
    input wire clk, reset, tick, write,
    input wire [3:0] address,
    input wire [7:0] data,
    input wire [127:0] keys,
    output wire [7:0] port_a, port_b, latch, sound_data,
    output wire [1:0] scroll,
    output wire sound_write, irq
);
    wire [7:0] drive_a, bus_drive_a, bus_port_b, pa_input, ready_data;
    wire ca2, bus_ca2, enabled, touch;
    Via6522 via(.clk(clk), .reset(reset), .io_tick(tick), .io_phi2(tick), .io_write(write),
        .io_read(1'b0), .io_address(address), .io_data(data),
        .io_portAIn(pa_input), .io_portBIn(8'hff), .io_ca1(1'b0), .io_ca2(bus_ca2), .io_ca2AfterWrite(write && (ca2!=bus_ca2)), .io_cb1(1'b0), .io_cb1Pulse(1'b0), .io_cb2(1'b0),
        .io_portA(port_a), .io_portB(port_b), .io_driveA(drive_a),
        .io_busDriveA(bus_drive_a), .io_busPortB(bus_port_b), .io_result(), .io_irq(irq));
    BbcSystemPins pins(.clk(clk), .reset(reset), .io_keys(keys), .io_driveA(drive_a),
        .io_busDriveA(bus_drive_a), .io_busPortB(bus_port_b),
        .io_portAIn(pa_input), .io_ca2(ca2), .io_busCa2(bus_ca2), .io_scroll(scroll), .io_latch(latch),
        .io_soundEnabled(enabled), .io_soundData(ready_data));
    assign touch=write && (address==0 || address==1 || address==2 || address==3 || address==15);
    BbcSoundBus sound(.clk(clk), .reset(reset), .audio_newClockEnable(1'b1), .io_enabled(enabled), .io_touch(touch),
        .io_data(ready_data), .io_write(sound_write), .io_acceptedData(sound_data));
endmodule
