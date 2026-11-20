module bbc_cpu_ce(input CE,clk,reset,input [7:0] DI,input IRQ,NMI,RDY,BBC_WARM,BBC_WARM_BEGIN,
 output [15:0] AB,BBC_PC,output [7:0] DO,BBC_A,BBC_X,BBC_Y,BBC_S,BBC_P,output WE,BBC_FETCH,BBC_SYNC);
(* anyseq *) reg [15:0] address,pc;
(* anyseq *) reg [7:0] value,a,x,y,s,p;
(* anyseq *) reg writing,instruction,sync;
assign AB=address;assign BBC_PC=pc;assign DO=value;assign BBC_A=a;assign BBC_X=x;assign BBC_Y=y;assign BBC_S=s;assign BBC_P=p;
assign WE=writing;assign BBC_FETCH=instruction;assign BBC_SYNC=sync;
endmodule
