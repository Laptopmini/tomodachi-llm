// Post-script. Args: <outfile> <cmd>...  cmds:
//   decomp:<addr>   decompile function containing addr
//   callers:<addr>  list call sites of function at addr (with caller names)
//   ptrs:<addr>     find 8-byte little-endian pointers to addr in initialized memory
//   str:<text>      address(es) of exact defined string + direct refs
//   sym:<text>      symbols whose name contains text, with their references
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import ghidra.program.util.DefinedStringIterator;
import java.io.*;
import java.util.*;

public class TomodachiLlmQuery extends GhidraScript {
    DecompInterface di;
    PrintWriter out;
    Address A(String s) { return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(Long.parseUnsignedLong(s.replace("0x",""),16)); }
    String fn(Address a) { Function f = getFunctionContaining(a); return f == null ? "?" : f.getName() + "@" + f.getEntryPoint(); }
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        out = new PrintWriter(new FileWriter(args[0], true));
        di = new DecompInterface(); di.openProgram(currentProgram);
        for (int i = 1; i < args.length; i++) {
            String c = args[i]; int k = c.indexOf(':'); String op = c.substring(0,k), v = c.substring(k+1);
            out.println("#### " + c);
            try {
            switch (op) {
                case "decomp": {
                    Function f = getFunctionContaining(A(v));
                    if (f == null) { createFunction(A(v), null); f = getFunctionContaining(A(v)); }
                    if (f == null) { out.println("no function"); break; }
                    DecompileResults r = di.decompileFunction(f, 180, monitor);
                    out.println("---- " + f.getName() + " @ " + f.getEntryPoint() + " size " + f.getBody().getNumAddresses());
                    out.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "(failed " + r.getErrorMessage() + ")");
                    break; }
                case "callers": {
                    for (Reference r : currentProgram.getReferenceManager().getReferencesTo(A(v)))
                        out.println("  " + r.getReferenceType() + " from " + r.getFromAddress() + " in " + fn(r.getFromAddress()));
                    break; }
                case "ptrs": {
                    long target = A(v).getOffset();
                    byte[] pat = new byte[8];
                    for (int b = 0; b < 8; b++) pat[b] = (byte)(target >>> (8*b));
                    Memory m = currentProgram.getMemory();
                    for (MemoryBlock blk : m.getBlocks()) {
                        if (!blk.isInitialized()) continue;
                        Address s = blk.getStart();
                        while (s != null && s.compareTo(blk.getEnd()) <= 0) {
                            Address h = m.findBytes(s, blk.getEnd(), pat, null, true, monitor);
                            if (h == null) break;
                            out.println("  ptr at " + h + " (" + blk.getName() + ")");
                            for (Reference r : currentProgram.getReferenceManager().getReferencesTo(h))
                                out.println("     ref from " + r.getFromAddress() + " in " + fn(r.getFromAddress()));
                            s = h.add(1);
                        }
                    }
                    break; }
                case "sym": {
                    SymbolIterator it = currentProgram.getSymbolTable().getAllSymbols(true);
                    while (it.hasNext()) {
                        Symbol sy = it.next();
                        if (!sy.getName(true).contains(v)) continue;
                        out.println("  " + sy.getName(true) + " @ " + sy.getAddress());
                        for (Reference r : sy.getReferences())
                            out.println("     ref from " + r.getFromAddress() + " in " + fn(r.getFromAddress()));
                    }
                    break; }
                case "str": {
                    for (Data d : DefinedStringIterator.forProgram(currentProgram)) {
                        Object o = d.getValue();
                        if (o instanceof String && o.equals(v)) {
                            out.println("  at " + d.getAddress());
                            for (Reference r : currentProgram.getReferenceManager().getReferencesTo(d.getAddress()))
                                out.println("     ref from " + r.getFromAddress() + " in " + fn(r.getFromAddress()));
                        }
                    }
                    break; }
            }
            } catch (Exception e) { out.println("error: " + e); }
            out.flush();
        }
        out.close();
    }
}
