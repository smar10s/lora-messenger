include config.mk

SKETCH_DIR := sketches/$(SKETCH)

.PHONY: build flash monitor list clean help

build:
	arduino-cli compile --fqbn $(FQBN) $(SKETCH_DIR)

flash: build
ifndef PORT
	$(error No serial port found. Plug in the board or set PORT=)
endif
	arduino-cli upload --fqbn $(FQBN) -p $(PORT) $(SKETCH_DIR)

monitor:
ifndef PORT
	$(error No serial port found. Plug in the board or set PORT=)
endif
	arduino-cli monitor -p $(PORT) -c baudrate=$(BAUD)

list:
	@ls -1 sketches/

clean:
	@for d in sketches/*/; do \
		arduino-cli compile --fqbn $(FQBN) --clean $$d 2>/dev/null; \
	done
	@echo "Build caches cleared."

help:
	@echo "Usage:"
	@echo "  make build   [SKETCH=name]  - compile sketch"
	@echo "  make flash   [SKETCH=name]  - compile + upload"
	@echo "  make monitor                - serial monitor ($(BAUD) baud)"
	@echo "  make flash monitor          - flash then monitor"
	@echo "  make list                   - show available sketches"
	@echo "  make clean                  - clear build caches"
	@echo ""
	@echo "Config (override on command line or edit config.mk):"
	@echo "  SKETCH = $(SKETCH)"
	@echo "  PORT   = $(or $(PORT),(not detected))"
	@echo "  FQBN   = $(FQBN)"
