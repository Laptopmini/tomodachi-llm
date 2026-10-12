// Create functions at each address listed in a file (arg 0) that has no instruction yet.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.program.model.address.*;
import java.nio.file.*;
public class TomodachiLlmCreateAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        int made = 0, total = 0;
        for (String line : Files.readAllLines(Paths.get(getScriptArgs()[0]))) {
            Address t = toAddr(Long.parseUnsignedLong(line.trim().substring(2), 16)); total++;
            if (getInstructionAt(t) != null || getDataAt(t) != null && getDataAt(t).isDefined()) continue;
            if (!new DisassembleCommand(t, null, true).applyTo(currentProgram, monitor)) continue;
            if (getFunctionAt(t) == null && new CreateFunctionCmd(t).applyTo(currentProgram, monitor)) made++;
        }
        println("targets " + total + ", functions created: " + made);
    }
}
