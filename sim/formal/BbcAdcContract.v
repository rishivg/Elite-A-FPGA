// Independent elapsed-time model of the declared BBC digital ADC profile.
// The actual device uses a countdown. Joint state invariants connect the two;
// no output equality is assumed and every input may change at every edge.
module AdcContract(
  input clk, reset, tick, read, write,
  input [1:0] address,
  input [7:0] data,
  input [63:0] channels,
  output equivalent
);
  wire [7:0] result;
  wire eocN, eocPulse, readRace, eocBusN;
  wire [3:0] actualControl, actualResultControl;
  wire [13:0] remaining;
  wire actualBusy, actualCompleted;
  wire [15:0] actualSample;
  Adc7002 actual(
    .clk(clk), .reset(reset), .io_tick(tick), .io_read(read), .io_write(write),
    .io_address(address), .io_data(data), .io_channels(channels),
    .io_result(result), .io_eocN(eocN), .io_eocPulse(eocPulse),
    .io_readRace(readRace), .io_eocBusN(eocBusN),
    .proofControl(actualControl), .proofResultControl(actualResultControl),
    .proofRemaining(remaining), .proofBusy(actualBusy),
    .proofCompleted(actualCompleted), .proofSample(actualSample)
  );

  reg [3:0] selected = 0, completedControl = 0;
  reg [13:0] elapsed = 0;
  reg pending = 0, done = 1;
  reg [15:0] saved = 0;
  // No claim about state before the fixture's reset has been observed.
  reg started = 0;
  always @(posedge clk) if (reset) started <= 1;
  wire [13:0] duration = selected[3] ? 14'd10000 : 14'd4000;
  wire finishing = pending && tick && elapsed == duration - 1;
  wire start = write && address == 0;
  wire acknowledge = read && address == 1;
  wire [15:0] channelValue = channels >> (16 * selected[1:0]);
  wire [15:0] latched = channelValue & 16'hfff0;
  always @(posedge clk or posedge reset)
    if (reset) begin
      selected <= 0;
      completedControl <= 0;
      elapsed <= 0;
      pending <= 0;
      done <= 1;
      saved <= 0;
    end else begin
      if (pending && tick) begin
        if (finishing) begin
          pending <= 0;
          done <= 1;
          completedControl <= selected;
          saved <= latched;
          elapsed <= 0;
        end else elapsed <= elapsed + 1;
      end
      // Completion transfers the old result before a coincident restart.
      if (start) begin
        selected <= data[3:0];
        elapsed <= 0;
        pending <= 1;
        done <= 0;
      end
      if (acknowledge) done <= 0;
    end

  wire [15:0] visible = finishing ? latched : saved;
  wire [3:0] visibleControl = finishing ? selected : completedControl;
  wire [7:0] status = {
    !(done || finishing), !(pending && !finishing), visible[15:14], visibleControl
  };
  wire [7:0] expectedResult = address == 0 ? status :
    address == 1 ? visible[15:8] : visible[7:0];
  assign equivalent = !started || (
    actualControl == selected && actualResultControl == completedControl &&
    actualBusy == pending && actualCompleted == done && actualSample == saved &&
    (!pending || remaining == duration - elapsed) &&
    result == expectedResult && eocN == !done &&
    eocPulse == (finishing && (start || acknowledge)) &&
    readRace == (finishing && read) &&
    eocBusN == ((!done && !finishing) || start || acknowledge)
  );
endmodule
