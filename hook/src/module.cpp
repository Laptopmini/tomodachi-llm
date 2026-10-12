// Module identity and rtld work area for the plugin (pattern from Skyline-LTD source/module.cpp, MIT).

#define MODULE_NAME "TomodachiLLM"
#define MODULE_NAME_LEN 12

// rtld working object
__attribute__((section(".bss"))) char __nx_module_runtime[0xD0];

struct ModuleName {
    int unknown;
    int name_length;
    char name[MODULE_NAME_LEN + 1];
};

__attribute__((section(".rodata.module_name"), used))
const ModuleName module_name = {.unknown = 0, .name_length = MODULE_NAME_LEN, .name = MODULE_NAME};
