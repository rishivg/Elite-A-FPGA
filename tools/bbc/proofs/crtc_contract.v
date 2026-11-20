// Independent imperative character-step contract following the published
// Hitachi-model operation order. This is a declared digital contract, not
// a replacement for the independently executed original video engine.
module CrtcHistoryReference(input clk,reset,enable,input [143:0] registers,
 output [105:0] state, output [13:0] busAddress,output busVsync,frame);
wire [7:0] r0=registers[7:0],r1=registers[15:8],r2=registers[23:16],
 r3=registers[31:24],r4=registers[39:32],r5=registers[47:40],
 r6=registers[55:48],r7=registers[63:56],r8=registers[71:64],
 r9=registers[79:72],r10=registers[87:80],r11=registers[95:88],
 r12=registers[103:96],r13=registers[111:104];
reg [7:0] h,nh;
reg [6:0] row,nrow;
reg [4:0] raster,nraster,adjust,nadjust,count,ncount;
reg [9:0] line,nline;
reg [13:0] address,na,lineAddress,nla,nextAddress,nnla;
reg adjusting,nadjusting,sync,nsync,hadSync,nhadSync,main,nmain,
 check,ncheck,adjustEnd,nadjustEnd,dummy,ndummy,parity,nparity,
 on,non,off,noff,visible,nvisible,odd,nodd,ra0,nra0,active,nactive,
 first,nfirst,hs,nhs;
reg [3:0] syncCount,nsyncCount,hcount,nhcount;
reg starts,ends,newFrame;
wire iv=r8[1:0]==3;
wire syncPoint=!r8[0] || odd || {1'b0,h}==(({1'b0,r0}+9'd1)>>1);
assign state={h,row,raster,line,adjusting,adjust,lineAddress,nextAddress,
 address,sync,syncCount,hadSync,main,check,adjustEnd,dummy,parity,count,
 on,off,visible,odd,ra0,active,first,hs,hcount};
assign busAddress=na;
assign busVsync=nsync;
assign frame=newFrame;
always @* begin
 nh=h;nrow=row;nraster=raster;nline=line;nadjusting=adjusting;
 nadjust=adjust;nla=lineAddress;nnla=nextAddress;na=address;
 nsync=sync;nsyncCount=syncCount;nhadSync=hadSync;nmain=main;
 ncheck=check;nadjustEnd=adjustEnd;ndummy=dummy;nparity=parity;
 ncount=count;non=on;noff=off;nvisible=visible;nodd=odd;nra0=ra0;
 nactive=active;nfirst=first;nhs=hs;nhcount=hcount;
 starts=0;ends=0;newFrame=0;
 if(enable)begin
  // Address capture precedes the unconditional increment.
  if(h==r1)nnla=address;
  na=address+14'd1;
  if(sync && syncCount==r3[7:4] && syncPoint)begin nsync=0;ends=1;end
  if(row==r7 && !nsync && !hadSync && syncPoint)begin nsync=1;starts=1;end
  if(starts && !ends)begin nhadSync=1;nsyncCount=0;end
  if(check)begin
   ncheck=0;
   if(main)begin
    if(adjust==r5)nadjustEnd=1;
    nadjust=adjust+5'd1;
   end
  end
  if(h==1)begin
   if(row==r4 && raster==r9)begin nmain=1;nadjust=0;end
   ncheck=1;
  end
  if(h==r0)begin
   nh=0;nline=line+10'd1;nfirst=0;
   if(raster==r11)noff=1;
   nsyncCount=nsyncCount+4'd1;
   if(raster==r9)nla=nnla;
   nraster=iv?((raster+5'd2)&5'd30):(raster+5'd1);
   if(!adjusting && raster==r9)begin
    nrow=row+7'd1;nraster=0;nhadSync=0;non=0;noff=0;
   end
   if(nmain && !nadjustEnd)nadjusting=1;
   if(dummy)newFrame=1;
   if(nadjustEnd)begin
    nadjusting=0;
    if(r8[0] && parity)ndummy=1;
    else newFrame=1;
   end
   if(newFrame)begin
    nrow=0;nraster=0;nline=0;nadjusting=0;nmain=0;
    nadjustEnd=0;ndummy=0;nfirst=1;nactive=1;nodd=parity;
    nhadSync=0;non=0;noff=0;
    nla={r12[5:0],r13};nnla={r12[5:0],r13};
    nvisible=r10[6:5]==0 || (r10[6:5]==2 && count[3]) ||
     (r10[6:5]==3 && count[4]);
   end
   na=nla;
   if(nraster==r10[4:0])non=1;
   // The pin latches before the end-of-display frame count increments.
   nra0=nraster[0] ^ (iv && parity);
  end else nh=h+8'd1;
  if(nrow==r6 && !nfirst && active)begin
   nactive=0;nparity=!parity;ncount=count+5'd1;
  end
  // Digital HSYNC ends before a coincident new pulse starts. The JS
  // monitor model's half-width beam callback is a separate observation.
  if(hs)begin
   nhcount=hcount+4'd1;
   if(nhcount==r3[3:0])nhs=0;
  end
  if(h==r2 && !nhs)begin nhs=1;nhcount=0;end
 end
end
always @(posedge clk or posedge reset)begin
 if(reset)begin
  h<=0;row<=0;raster<=0;line<=0;adjusting<=0;adjust<=0;
  lineAddress<=0;nextAddress<=0;address<=0;sync<=0;syncCount<=0;
  hadSync<=0;main<=0;check<=0;adjustEnd<=0;dummy<=0;parity<=0;
  count<=0;on<=0;off<=0;visible<=0;odd<=0;ra0<=0;active<=0;
  first<=1;hs<=0;hcount<=0;
 end else begin
  h<=nh;row<=nrow;raster<=nraster;line<=nline;adjusting<=nadjusting;
  adjust<=nadjust;lineAddress<=nla;nextAddress<=nnla;address<=na;
  sync<=nsync;syncCount<=nsyncCount;hadSync<=nhadSync;main<=nmain;
  check<=ncheck;adjustEnd<=nadjustEnd;dummy<=ndummy;parity<=nparity;
  count<=ncount;on<=non;off<=noff;visible<=nvisible;odd<=nodd;
  ra0<=nra0;active<=nactive;first<=nfirst;hs<=nhs;hcount<=nhcount;
 end
end
endmodule
