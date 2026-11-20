// Execute a real reset and register/stack initialization prefix. The caller
// supplies independent target bus responses and its retirement sample phase.
module BbcCpuInterruptStreamPrefix #(parameter ALLOW_NMI=1)(
  input clk, input irqPin, input nmiPin,
  input [7:0] a, x, y, s, p, opcode,
  input [15:0] base, targetAddress,
  input [7:0] targetRead, targetValue,
  input targetWrite,
  input [5:0] retirementStep,
  output [5:0] step,
  output reset, busMatches, prefixReady, writing, instruction,
  output [15:0] address, pc,
  output [7:0] value, accumulator, indexX, indexY, stack, flags, retiredFlags
);
  reg [3:0] resetEdges = 0;
  reg [5:0] age = 0;
  reg [7:0] readData = 0;
  reg retiredI = 0;
  assign reset = resetEdges < 8;
  assign step = age;

  BbcCpu actual(
    .warm_reset(1'b0),
    .clk(clk), .reset(reset), .enable(1'b1), .irq(age>=26 ? irqPin : 1'b0), .nmi(age>=26 && ALLOW_NMI ? nmiPin : 1'b0),
    .read_data(readData), .address(address), .pc(pc), .write_data(value),
    .write_enable(writing), .instruction(instruction),
    .accumulator(accumulator), .index_x(indexX), .index_y(indexY),
    .stack_pointer(stack), .flags(flags)
  );

  reg [15:0] requestedAddress;
  reg [7:0] returnedData;
  always @* begin
    requestedAddress = targetAddress;
    returnedData = targetRead;
    case (age)
       0, 1: begin requestedAddress = 0; returnedData = 8'hea; end
       2: begin requestedAddress = 16'h0100; returnedData = 8'hea; end
       3: begin requestedAddress = 16'h01ff; returnedData = 8'hea; end
       4: begin requestedAddress = 16'h01fe; returnedData = 8'hea; end
       5: begin requestedAddress = 16'hfffc; returnedData = base[7:0]; end
       6: begin requestedAddress = 16'hfffd; returnedData = base[15:8]; end
       7: begin requestedAddress = base; returnedData = 8'ha2; end
       8: begin requestedAddress = base + 1; returnedData = s; end
       9: begin requestedAddress = base + 2; returnedData = 8'h9a; end
      10, 11: begin requestedAddress = base + 3; returnedData = 8'ha9; end
      12: begin requestedAddress = base + 4; returnedData = p; end
      13: begin requestedAddress = base + 5; returnedData = 8'h48; end
      14, 16: begin requestedAddress = base + 6; returnedData = 8'ha9; end
      15: begin requestedAddress = 16'h0100 | {8'd0, s}; returnedData = p; end
      17: begin requestedAddress = base + 7; returnedData = a; end
      18: begin requestedAddress = base + 8; returnedData = 8'ha2; end
      19: begin requestedAddress = base + 9; returnedData = x; end
      20: begin requestedAddress = base + 10; returnedData = 8'ha0; end
      21: begin requestedAddress = base + 11; returnedData = y; end
      22: begin requestedAddress = base + 12; returnedData = 8'h28; end
      23: begin requestedAddress = base + 13; returnedData = opcode; end
      24: begin requestedAddress = 16'h0100 | {8'd0, (s - 8'd1)}; returnedData = 8'hea; end
      25: begin requestedAddress = 16'h0100 | {8'd0, s}; returnedData = p; end
    endcase
  end
  wire requestedWrite = age < 26 ? age == 15 : targetWrite;
  wire [7:0] writtenValue = age < 26 ? p : targetValue;
  // The first target DECODE commits the preceding PLP. CLI/SEI can already
  // update I on that edge; other target architectural writes occur later.
  wire [7:0] initialP = opcode == 8'h78 ? p | 8'h04 :
                       opcode == 8'h58 ? p & 8'hfb : p;
  assign prefixReady = age != 28 || (
    accumulator == a && indexX == x && indexY == y && stack == s &&
    (flags & 8'hcf) == (initialP & 8'hcf)
  );
  assign busMatches = address == requestedAddress && writing == requestedWrite &&
                      (!writing || value == writtenValue);

  // DI is the response registered after the preceding request edge, matching
  // the maintained native bus driver. No internal CPU register is assigned.
  always @(posedge clk) begin
    if (reset) begin
      resetEdges <= resetEdges + 1;
      age <= 0;
      readData <= 8'hea;
    end else begin
      if (age < 30) age <= age + 1;
      readData <= returnedData;
    end
    if (age == retirementStep) retiredI <= flags[2];
  end
  // The core can apply a prefetched CLI/SEI on the retirement DECODE edge.
  // Sample I before that edge, and the other architectural flags after it.
  assign retiredFlags = (flags & 8'hcb) | {5'd0, retiredI, 2'd0};
endmodule
