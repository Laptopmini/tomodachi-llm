// Pre-script: turn off slow analyzers we don't need for string/xref/decompile work.
import ghidra.app.script.GhidraScript;
public class TomodachiLlmFastOptions extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] off = {"Decompiler Switch Analysis", "Function ID", "Embedded Media",
                        "Decompiler Parameter ID", "Aggressive Instruction Finder",
                        "Condense Filler Bytes", "Create Address Tables"};
        for (String o : off) {
            try { setAnalysisOption(currentProgram, o, "false"); println("off: " + o); }
            catch (Exception e) { println("skip: " + o + " (" + e.getMessage() + ")"); }
        }
    }
}
