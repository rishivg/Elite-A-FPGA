// Independent documented NMOS architectural semantics. These byte/nibble
// formulas define expected values; they do not instantiate or copy the RTL ALU.
module BbcCpuArchitectureContract(
 input [7:0] a,x,y,s,p,operand,opcode,
 input isRmw,isIncDec,
 output reg [7:0] expectedA,expectedX,expectedY,expectedS,expectedP,memoryResult
);
wire [7:0] evalA=isRmw ? operand : a;
wire [7:0] evalX=isRmw ? operand : x;
reg [7:0] nz;
reg changeNz;
reg [8:0] wide;
reg [7:0] lowDecimal,highDecimal,compared;
always @* begin
 expectedA=evalA;expectedX=evalX;expectedY=y;expectedS=s;expectedP=p;nz=0;changeNz=0;wide=0;lowDecimal=0;highDecimal=0;compared=evalA;memoryResult=0;
 case(opcode)
 8'h24:begin expectedP=(p&8'h3d)|(operand&8'hc0)|((evalA&operand)==0 ? 8'h02 : 8'h00);end
 8'h18:expectedP=p&8'hfe;
 8'h38:expectedP=p|8'h01;
 8'hd8:expectedP=p&8'hf7;
 8'hf8:expectedP=p|8'h08;
 8'hb8:expectedP=p&8'hbf;
 8'h58:expectedP=p&8'hfb;
 8'h78:expectedP=p|8'h04;
 8'hca:begin expectedX=evalX-8'd1;nz=expectedX;changeNz=1;end
 8'h88:begin expectedY=y-8'd1;nz=expectedY;changeNz=1;end
 8'he8:begin expectedX=evalX+8'd1;nz=expectedX;changeNz=1;end
 8'hc8:begin expectedY=y+8'd1;nz=expectedY;changeNz=1;end
 8'h8a:begin expectedA=evalX;nz=expectedA;changeNz=1;end
 8'h98:begin expectedA=y;nz=expectedA;changeNz=1;end
 8'haa:begin expectedX=evalA;nz=expectedX;changeNz=1;end
 8'ha8:begin expectedY=evalA;nz=expectedY;changeNz=1;end
 8'h9a:expectedS=evalX;
 8'hba:begin expectedX=s;nz=expectedX;changeNz=1;end
 8'h0a:begin expectedA={evalA[6:0],1'b0};expectedP=(p&8'hfe)|{7'd0,evalA[7]};nz=expectedA;changeNz=1;end
 8'h4a:begin expectedA={1'b0,evalA[7:1]};expectedP=(p&8'hfe)|{7'd0,evalA[0]};nz=expectedA;changeNz=1;end
 8'h2a:begin expectedA={evalA[6:0],p[0]};expectedP=(p&8'hfe)|{7'd0,evalA[7]};nz=expectedA;changeNz=1;end
 8'h6a:begin expectedA={p[0],evalA[7:1]};expectedP=(p&8'hfe)|{7'd0,evalA[0]};nz=expectedA;changeNz=1;end
 8'h09:begin expectedA=evalA|operand;nz=expectedA;changeNz=1;end
 8'h29:begin expectedA=evalA&operand;nz=expectedA;changeNz=1;end
 8'h49:begin expectedA=evalA^operand;nz=expectedA;changeNz=1;end
 8'ha9:begin expectedA=operand;nz=expectedA;changeNz=1;end
 8'ha2:begin expectedX=operand;nz=expectedX;changeNz=1;end
 8'ha0:begin expectedY=operand;nz=expectedY;changeNz=1;end
 8'hc9,8'he0,8'hc0:begin
  compared=opcode==8'he0 ? evalX : opcode==8'hc0 ? y : evalA;
  wide={1'b0,compared}-{1'b0,operand};
  expectedP=(p&8'hfe)|{7'd0,!wide[8]};nz=wide[7:0];changeNz=1;
 end
 8'h69:begin
  wide={1'b0,evalA}+{1'b0,operand}+{8'd0,p[0]};
  expectedP=p&8'h3c;
  if(p[3])begin
   lowDecimal={4'd0,evalA[3:0]}+{4'd0,operand[3:0]}+{7'd0,p[0]};
   if(lowDecimal>9)lowDecimal=lowDecimal+6;
   highDecimal={4'd0,evalA[7:4]}+{4'd0,operand[7:4]}+(lowDecimal>15);
   if(wide[7:0]==0)expectedP=expectedP|8'h02;
   else if(highDecimal[3])expectedP=expectedP|8'h80;
   if((~(evalA^operand)&(evalA^{highDecimal[3:0],4'd0})&8'h80)!=0)expectedP=expectedP|8'h40;
   if(highDecimal>9)highDecimal=highDecimal+6;
   if(highDecimal>15)expectedP=expectedP|8'h01;
   expectedA={highDecimal[3:0],lowDecimal[3:0]};
  end else begin
   expectedA=wide[7:0];nz=expectedA;changeNz=1;
   expectedP=expectedP|{7'd0,wide[8]};
   if((~(evalA^operand)&(evalA^expectedA)&8'h80)!=0)expectedP=expectedP|8'h40;
  end
 end
 8'he9:begin
  wide={1'b0,evalA}-{1'b0,operand}-{8'd0,!p[0]};
  expectedP=(p&8'h3c)|{7'd0,!wide[8]};nz=wide[7:0];changeNz=1;
  if(((evalA^operand)&(evalA^wide[7:0])&8'h80)!=0)expectedP=expectedP|8'h40;
  if(p[3])begin
   lowDecimal={4'd0,evalA[3:0]}-{4'd0,operand[3:0]}-{7'd0,!p[0]};
   if(lowDecimal[7])lowDecimal=lowDecimal-6;
   highDecimal={4'd0,evalA[7:4]}-{4'd0,operand[7:4]}-{7'd0,lowDecimal[7]};
   if(highDecimal[7])highDecimal=highDecimal-6;
   expectedA={highDecimal[3:0],lowDecimal[3:0]};
  end else expectedA=wide[7:0];
 end
 endcase
 if(changeNz)expectedP=(expectedP&8'h7d)|(nz[7]?8'h80:8'h00)|(nz==0?8'h02:8'h00);
 memoryResult=isIncDec ? expectedX : expectedA;
 if(isRmw)begin expectedA=a;expectedX=x;expectedY=y;end
end
endmodule
