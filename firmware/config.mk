# RAK11300 + RAK19007 WisBlock configuration
FQBN    := rakwireless:mbed_rp2040:WisCoreRAK11300Board
SKETCH  := LoRaMessenger
BAUD    := 115200

# Auto-detect port; override with: make flash PORT=/dev/cu.usbmodemXXXX
PORT    := $(firstword $(wildcard /dev/cu.usbmodem*))
