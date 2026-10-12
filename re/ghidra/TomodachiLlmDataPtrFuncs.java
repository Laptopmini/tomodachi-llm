// Create functions at every 8-aligned pointer in data blocks that targets .text with no code yet.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.listing.*;
public class TomodachiLlmDataPtrFuncs extends GhidraScript {
    @Override
    public void run() throws Exception {
        Memory m = currentProgram.getMemory();
        MemoryBlock text = m.getBlock(".text");
        long ts = text.getStart().getOffset(), te = text.getEnd().getOffset();
        AddressSet todo = new AddressSet();
        int seen = 0;
        for (MemoryBlock b : m.getBlocks()) {
            if (!b.isInitialized() || !b.getName().startsWith(".data") && !b.getName().startsWith(".rodata")) continue;
            long s = (b.getStart().getOffset() + 7) & ~7L;
            for (long a = s; a + 8 <= b.getEnd().getOffset() + 1; a += 8) {
                long v = m.getLong(toAddr(a));
                if (v >= ts && v <= te && (v & 3) == 0) {
                    Address t = toAddr(v); seen++;
                    if (getInstructionAt(t) == null && getDataAt(t) == null || getInstructionAt(t) == null) todo.add(t);
                }
            }
        }
        println("pointers into text: " + seen + ", new targets: " + todo.getNumAddresses());
        int made = 0;
        for (Address t : todo.getAddresses(true)) {
            if (monitor.isCancelled()) break;
            if (getInstructionAt(t) != null) continue;
            if (!new DisassembleCommand(t, null, true).applyTo(currentProgram, monitor)) continue;
            if (getFunctionAt(t) == null && new CreateFunctionCmd(t).applyTo(currentProgram, monitor)) made++;
        }
        println("functions created: " + made);
    }
}
