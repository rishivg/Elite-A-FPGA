// Independent scheduled-deadline contract for the active cold-reset WE fixture.
module SoundBusContract(input clk, reset, clockEnable, enabled, touch,
 input [7:0] data, output equivalent);
 wire [16:0] state;
 wire write;
 wire [7:0] accepted;
 BbcSoundBus actual(.clk(clk),.reset(reset),.audio_newClockEnable(clockEnable),
 .io_enabled(enabled),.io_touch(touch),.io_data(data),
 .io_write(write),.io_acceptedData(accepted),.proofState(state));
 reg [7:0] lastData=0;
 reg lastEnabled=0;
 reg pending=1;
 reg [7:0] remaining=223;
 wire changed=touch || data!=lastData || !lastEnabled;
 wire schedule=enabled && changed;
 wire completing=pending && remaining==0;
 initial assume(reset);
 always @(posedge clk or posedge reset)
 if(reset) begin lastData<=0;lastEnabled<=0;pending<=1;remaining<=223;end
 else if(clockEnable) begin
  lastData<=data;lastEnabled<=enabled;
  if(pending && !completing) remaining<=remaining-1;
  if(!pending || completing) begin
   pending<=schedule;
   remaining<=schedule ? 8'd223 : 8'd0;
  end
 end
 wire [7:0] elapsed=pending ? 8'd224-remaining : 8'd225;
 assign equivalent=state=={lastData,lastEnabled,elapsed}
 && write==(lastEnabled && completing) && accepted==lastData
 && remaining<=223 && (pending || remaining==0);
endmodule
