// Execute the actual common-clock 96 MHz machine with the shared capture driver.
#include "VBbcMachine96.h"
#if __has_include("BbcMachine96IdleFall.h")
#include "BbcMachine96IdleFall.h"
#endif
#define BBC_MACHINE_TYPE VBbcMachine96
#define BBC_MASTER_MULTIPLIER 3
#include "bbc.cpp"
