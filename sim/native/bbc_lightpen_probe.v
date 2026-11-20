// Exact generated device modules connected as in the BBC machine.
module BbcLightPenProbe (
    input wire clk, reset, tick,
    input wire [1:0] operation,
    input wire [3:0] address,
    input wire [7:0] data,
    input wire cb1, cb1Pulse,
    output wire [143:0] registers,
    output wire [13:0] ma,
    output wire [22:0] crtcState,
    output wire [7:0] pcr,
    output wire cb2Pin, cb2Output
);
    // Read-only observation of the generated CRTC address; no driver is added.
    assign ma = video.crtc_io_address;
    assign crtcState = {video.crtc_io_displayActive, video.crtc_io_hsync,
        video.crtc_io_vsync, video.crtc_io_raster, video.crtc_io_row,
        video.crtc_io_horizontal};
    wire vsync, cb2Rise;
    Via6522 via(
        .clk(clk), .reset(reset), .io_tick(tick), .io_phi2(tick),
        .io_read(1'b0), .io_write(operation == 3),
        .io_address(address), .io_data(data), .io_result(pcr),
        .io_portAIn(8'hff), .io_portBIn(8'hff),
        .io_ca1(vsync), .io_ca2(1'b0), .io_ca2AfterWrite(1'b0),
        .io_cb1(cb1), .io_cb1Pulse(cb1Pulse), .io_cb2(1'b0),
        .io_cb2Pin(cb2Pin), .io_cb2Output(cb2Output), .io_cb2Rise(cb2Rise)
    );
    BbcVideo video(
        .clk(clk), .reset(reset), .io_crtcWrite(operation == 1),
        .io_ulaWrite(operation == 2), .io_lightPenPulse(cb2Rise),
        .io_address(address[0]), .io_data(data),
        .io_scroll(2'b0), .io_memoryData(8'b0),
        .io_registers(registers), .io_busVsync(vsync)
    );
endmodule
