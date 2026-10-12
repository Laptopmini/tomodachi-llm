// Module header for an nn::ro plugin (NRO). nn::ro never runs this entry point;
// it only needs the MOD0 offset at +0x4. elf2nro writes the NRO header over
// 0x10..0x80, so the first 0x80 bytes are reserved.
// Layout follows Skyline-LTD's source/crt0.s (MIT, github.com/tlmodding/skyline-ltd).

.section ".text.crt0","ax"
.global __module_start
.extern __nx_module_runtime

__module_start:
    b .
    .word __nx_mod0 - __module_start

.org __module_start+0x80

.section ".rodata.mod0"
.global __nx_mod0
__nx_mod0:
    .ascii "MOD0"
    .word  __dynamic_start__    - __nx_mod0
    .word  __bss_start__        - __nx_mod0
    .word  __bss_end__          - __nx_mod0
    .word  0
    .word  0
    .word  __nx_module_runtime  - __nx_mod0
