# Builds the Tomodachi LLM Skyline plugin: build/tomodachi-llm.nro
#   make                 # build
#   make deploy          # copy into the work folder's tomodachi-llm-dev mod (seen by Ryujinx via symlink)
# Needs devkitPro (devkitA64 + switch-tools) and Skyline-LTD's headers at $(SKYLINE)
# (pinned: tlmodding/skyline-ltd v1.1.0 = 474b6d1, see docs/setup.md).

DEVKITPRO ?= /opt/devkitpro
SKYLINE   ?= $(HOME)/Documents/tomodachi-llm-work/src/skyline-ltd
WORK      ?= $(HOME)/Documents/tomodachi-llm-work
MOD_DIR   ?= $(WORK)/mods/tomodachi-llm-dev

PREFIX  := $(DEVKITPRO)/devkitA64/bin/aarch64-none-elf-
CXX     := $(PREFIX)g++
ELF2NRO := $(DEVKITPRO)/tools/bin/elf2nro

ARCH     := -march=armv8-a -mtune=cortex-a57 -mtp=soft -fPIC -ftls-model=local-exec -mno-outline-atomics
CXXFLAGS := $(ARCH) -std=gnu++20 -O2 -g -Wall -Wextra -Wno-main -Wno-unused-parameter \
            -ffunction-sections -fdata-sections -fno-rtti -fno-exceptions \
            -fno-asynchronous-unwind-tables -fno-unwind-tables -fno-threadsafe-statics \
            -D__SWITCH__ -I$(SKYLINE)/include -I$(DEVKITPRO)/libnx/include
ASFLAGS  := $(ARCH)
LDFLAGS  := $(ARCH) -shared -nostartfiles -nodefaultlibs -T plugin.ld \
            -Wl,--gc-sections -Wl,--build-id=sha1 -Wl,--version-script=exports.txt -Wl,-Map,build/tomodachi-llm.map
LIBS     := -lgcc

OBJS := build/crt0.o build/module.o build/main.o

all: build/tomodachi-llm.nro

build/%.o: src/%.cpp | build
	$(CXX) $(CXXFLAGS) -c $< -o $@

build/%.o: src/%.s | build
	$(CXX) $(ASFLAGS) -c $< -o $@

build/tomodachi-llm.elf: $(OBJS) plugin.ld exports.txt
	$(CXX) $(LDFLAGS) $(OBJS) $(LIBS) -o $@

build/tomodachi-llm.nro: build/tomodachi-llm.elf
	$(ELF2NRO) $< $@

build:
	mkdir -p build

deploy: build/tomodachi-llm.nro
	mkdir -p $(MOD_DIR)/romfs/skyline/plugins $(MOD_DIR)/romfs/tomodachi-llm
	cp build/tomodachi-llm.nro $(MOD_DIR)/romfs/skyline/plugins/tomodachi-llm.nro
	@[ -f $(MOD_DIR)/romfs/tomodachi-llm/config.ini ] || cp config.example.ini $(MOD_DIR)/romfs/tomodachi-llm/config.ini
	@echo deployed to $(MOD_DIR)

clean:
	rm -rf build

# Host-side unit test of the pure reline helpers (macOS/Linux clang or g++; no devkitPro needed).
test:
	@mkdir -p build
	$${HOSTCXX:-c++} -std=c++17 -Wall -Wno-unused-function -o build/host_test tests/host_test.cpp
	./build/host_test

.PHONY: all deploy clean test
