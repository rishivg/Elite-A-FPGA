# Digilent Arty A7 (Artix-7 XC7A100T) constraints — SCAFFOLD.
# Fill in real pin assignments for your VGA/HDMI PMOD. Reference:
# https://github.com/Digilent/digilent-xdc/blob/master/Arty-A7-100-Master.xdc

## 100 MHz system clock
# set_property -dict { PACKAGE_PIN E3  IOSTANDARD LVCMOS33 } [get_ports { clk }]
# create_clock -name sys_clk -period 10.0 [get_ports { clk }]

## VGA via PMOD DAC (example: PMOD JB/JC). TODO: assign hsync/vsync/r/g/b pins.
# set_property -dict { PACKAGE_PIN ... IOSTANDARD LVCMOS33 } [get_ports { hsync }]
# set_property -dict { PACKAGE_PIN ... IOSTANDARD LVCMOS33 } [get_ports { vsync }]
