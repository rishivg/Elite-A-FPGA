module disc_arithmetic(input [7:0] track,sector,parameter_word,input is_id,output equal);
wire [17:0] old_address=track*18'd2560+sector*18'd256;
wire [17:0] track_wide=track,sector_wide=sector;
wire [17:0] new_address=(track_wide<<11)+(track_wide<<9)+(sector_wide<<8);
wire [13:0] size=14'd128<<parameter_word[7:5];
wire [13:0] sectors={9'b0,parameter_word[4:0]};
wire [13:0] old_count=is_id?sectors*14'd4:size*sectors;
wire [13:0] new_count=is_id?sectors<<2:(sectors<<parameter_word[7:5])<<7;
assign equal=(old_address==new_address)&&(old_count==new_count);
endmodule
