// Independent elapsed-interval contract for the declared BBC digital PSG.
module PsgContract(input clk, reset, enabled, write, input [7:0] data,
 output equivalent);
 wire [127:0] state;
 wire [7:0] sample;
 wire [2:0] tone;
 wire noise, pdm;
 Psg actual(.clk(clk),.reset(reset),.audio_newClockEnable(enabled),
 .io_write(write),.io_data(data),.io_sample(sample),.io_tone(tone),
 .io_noise(noise),.io_pdm(pdm),.proofState(state));

 reg [9:0] period [0:2];
 reg [3:0] volume [0:3];
 reg [2:0] control=0;
 reg [1:0] selected=0;
 reg selectedVolume=0;
 reg [10:0] duration [0:2], elapsed [0:2];
 reg [2:0] square=0;
 reg [14:0] shift=15'h4000;
 reg [10:0] noiseDuration=1, noiseElapsed=0;
 reg noiseHalf=0;
 reg [6:0] halfRemaining=64;
 reg toneHalf=1;
 reg [8:0] integral=0;
 reg [9:0] nextPeriod [0:2];
 reg [3:0] nextVolume [0:3];
 reg [2:0] nextControl;
 reg resetShift;
 wire [1:0] commandChannel=data[7] ? data[6:5] : selected;
 wire commandVolume=data[7] ? data[4] : selectedVolume;
 integer i;
 always @* begin
  for(integer k=0;k<3;k=k+1) nextPeriod[k]=period[k];
  for(integer k=0;k<4;k=k+1) nextVolume[k]=volume[k];
  nextControl=control; resetShift=0;
  if(write) begin
   if(commandVolume) nextVolume[commandChannel]=data[3:0];
   else if(commandChannel==3) begin nextControl=data[2:0];resetShift=1;end
   else if(data[7]) nextPeriod[commandChannel]={period[commandChannel][9:4],data[3:0]};
   else nextPeriod[commandChannel]={data[5:0],period[commandChannel][3:0]};
  end
 end
 reg [10:0] newNoiseDuration;
 always @* case(nextControl[1:0])
  0:newNoiseDuration=32;
  1:newNoiseDuration=64;
  2:newNoiseDuration=128;
  3:newNoiseDuration=nextPeriod[2]==0 ? 11'd1 : {nextPeriod[2],1'b0};
 endcase
 wire halfTick=halfRemaining==1;
 wire fullTick=halfTick && toneHalf;
 wire noiseBoundary=noiseElapsed+11'd1==noiseDuration;
 wire [14:0] shiftBefore=resetShift ? 15'h4000 : shift;
 wire feedback=nextControl[2] ? shiftBefore[0]^shiftBefore[1] : shiftBefore[0];
 function [7:0] amplitude;
 input [3:0] attenuation;
 begin
 case(attenuation)
  0:amplitude=63;1:amplitude=50;2:amplitude=40;3:amplitude=32;
  4:amplitude=25;5:amplitude=20;6:amplitude=16;7:amplitude=13;
  8:amplitude=10;9:amplitude=8;10:amplitude=6;11:amplitude=5;
  12:amplitude=4;13:amplitude=3;14:amplitude=3;15:amplitude=0;
 endcase
 end
 endfunction
 wire [7:0] level=(square[0] ? amplitude(volume[0]) : 8'd0)+
 (square[1] ? amplitude(volume[1]) : 8'd0)+
 (square[2] ? amplitude(volume[2]) : 8'd0)+
 (shift[0] ? amplitude(volume[3]) : 8'd0);
 initial assume(reset);
 always @(posedge clk or posedge reset)
 if(reset) begin
  for(i=0;i<3;i=i+1) begin period[i]<=0;duration[i]<=1;elapsed[i]<=0;end
  for(i=0;i<4;i=i+1) volume[i]<=15;
  control<=0;selected<=0;selectedVolume<=0;square<=0;shift<=15'h4000;
  noiseDuration<=1;noiseElapsed<=0;noiseHalf<=0;
  halfRemaining<=64;toneHalf<=1;integral<=0;
 end else if(enabled) begin
  for(i=0;i<3;i=i+1) period[i]<=nextPeriod[i];
  for(i=0;i<4;i=i+1) volume[i]<=nextVolume[i];
  control<=nextControl;
  if(write && data[7]) begin selected<=data[6:5];selectedVolume<=data[4];end
  integral<={1'b0,integral[7:0]}+{1'b0,level};
  if(halfTick) begin halfRemaining<=64;toneHalf<=!toneHalf;end
  else halfRemaining<=halfRemaining-1;
  if(fullTick) for(i=0;i<3;i=i+1) begin
   if(elapsed[i]+11'd1==duration[i]) begin
    elapsed[i]<=0;duration[i]<=nextPeriod[i]==0 ? 11'd1024 : {1'b0,nextPeriod[i]};
    square[i]<=!square[i];
   end else elapsed[i]<=elapsed[i]+1;
  end
  if(resetShift) shift<=15'h4000;
  if(halfTick) begin
   if(noiseBoundary) begin
    noiseElapsed<=0;noiseDuration<=newNoiseDuration;noiseHalf<=!noiseHalf;
    if(!noiseHalf) shift<={feedback,shiftBefore[14:1]};
   end else noiseElapsed<=noiseElapsed+1;
  end
 end
 wire [9:0] count0=duration[0]-11'd1-elapsed[0];
 wire [9:0] count1=duration[1]-11'd1-elapsed[1];
 wire [9:0] count2=duration[2]-11'd1-elapsed[2];
 wire [10:0] noiseCount=noiseDuration-11'd1-noiseElapsed;
 wire [6:0] phase=toneHalf ? 8'd128-{1'b0,halfRemaining} : 7'd64-halfRemaining;
 assign equivalent=state=={period[0],period[1],period[2],volume[0],volume[1],volume[2],
 volume[3],control,selected,selectedVolume,phase,count0,count1,count2,
 square[0],square[1],square[2],shift,noiseCount,noiseHalf,integral}
 && sample==level && tone==square && noise==shift[0] && pdm==integral[8]
 && halfRemaining>=1 && halfRemaining<=64
 && duration[0]>=1 && duration[0]<=1024 && elapsed[0]<duration[0]
 && duration[1]>=1 && duration[1]<=1024 && elapsed[1]<duration[1]
 && duration[2]>=1 && duration[2]<=1024 && elapsed[2]<duration[2]
 && noiseDuration>=1 && noiseDuration<=2046 && noiseElapsed<noiseDuration;
endmodule
