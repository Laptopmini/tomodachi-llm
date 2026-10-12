# Pinned binary and offsets

Hooks target exactly this build. If any hash below changes, stop and re-plan (CLAUDE.md rule 5).

## Pinned game build

| Item | Value |
|---|---|
| Title ID | `010051F0207B2000` (confirmed by the owner in Ryubing, 2026-10-08) |
| Game version | 1.0.4 |
| `main` build ID (NSO module ID) | `b39fef373fb12154385d012aab3bc99ef2944470` |
| Dumped | 2026-10-08, Ryubing Canary 1.3.279 (Windows) → *Extract Data*, copied to `../dumps/1.0.4/` |

The build ID is what IPS patches key on (`exefs_patches/<name>/<build id>.ips`). RomFS `System/RegionLangMask.txt` ends with this same build ID (after the language list, `Normal` and `100`), so it ties a RomFS dump to its `main`. RomFS also has `System/AddressTable/Product.100.Nin_NX_NVN.atbl.byml.zs`, possibly the game's own address table (not yet examined).

## ExeFS (SHA-256)

| File | SHA-256 |
|---|---|
| `main` (NSO) | `54d552673a0e24463ba482077d22078164528ae3de14e6ee86969cdcf02080a2` |
| `sdk` (NSO) | `25cb36687a664e48f77fe8e8563ef13cc028336e621c6c8b396ee36f5199be64` |
| `rtld` (NSO) | `2f99073e1bd3cd95b0c2de6dfe83b3beb34b88a5e4be94570925abcc469ae3e7` |
| `main.npdm` | `fb2c05c2c890256eca34720da3ff78ec714b25aa573c6705a4e0ad7767d739aa` |

**No `subsdk0–9` in the game's ExeFS**, so every subsdk slot is free (Skyline-LTD uses `subsdk9`).

## Converted ELFs (nx2elf `a1760ba`, in `../dumps/1.0.4/elf/`)

| File | SHA-256 | Notes |
|---|---|---|
| `main.elf` | `a9e5f0786d543a372b03891dc43c9f91a18f6743823ed2535886e1e5ea701ccb` | AArch64 PIE, stripped, BuildID matches above |
| `sdk.elf` | (not recorded) | |
| `rtld.elf` | (not recorded) | |

## Offsets

Addresses are Ghidra's NSO layout (base `0x7100000000`); subtract the base for a module-relative offset (the form hook frameworks use). All were found by static analysis (from 2026-10-08); rows say which were later confirmed at runtime by our plugin (Ryujinx 1.3.351, macOS). Names are ours. A topic-organized summary is in `docs/game-research.md`.

### Message library (LibMessageStudio, MSBT)

| Address | Our name | Signature (inferred) | Evidence |
|---|---|---|---|
| `0x71005abc00` | `Msbt_GetTextByLabel` | `TextRef (Msbt* msbt /*x0*/, const char* label /*x1*/)`, 24-byte result returned through `x8`: `{const char16_t* text, s32 len, s32 ?, s64 index}`, or `{0, 0, -1}` if missing. **Hooked at runtime by plugin 0.1.0 (2026-10-08): works** | Label hash `h = h*0x492 + c` mod slot count over LBL1 (section index at `+0x20`), TXT2 offsets (section index at `+0x24`), char size from the encoding byte at `+0x10`. 34 `BL` + 7 `B` call sites. **Preferred first logging hook:** every label-based text fetch goes through it. Layout (decompiled 2026-10-11): the Msbt object holds the header pointer at `+0x0` and the message count at `+0x8`; the header has the section table at `+0x18` (0x18-byte entries: data pointer at `+0x0`, size in bytes at `+0xc`). Plugin 0.5.0 walks LBL1/TXT2 this way to read whole scripts (`hook/src/msbt.h`) |
| `0x71003999f0` | `Msbt_HasLabel` | `bool (MsbtRef*, const char* label)` | Same hash; returns found/not found. 29 callers |
| `0x71008581f0` | `Msbt_GetIndexByLabel` | `s32 (const u8* header, const char* label)`; −1 / −2 if missing. Takes the **file header** (what `Msbt*` points to), not the Msbt object (decompiled 2026-10-11) | Same hash. 20 callers, nearly all in the MessageShuffler (`0x7101e13258`, `0x7101e13d40`, `0x7101e14b10`, …). First words `b9802009 3100053f 54000740 aa1f03e8`. Hooked by plugin 0.6.1 to read caption files (`scripts=`) |
| `0x7101e19390` | `MsgMgr_GetText` | `void (TextRef* out, const char* mstxtName, const char** label)` | Looks the file up in the global message manager `*(0x7105230110)` (`0x71005d9fb0`), then calls `Msbt_GetTextByLabel`. Callers seen so far set UI layout text (e.g. `T_TitleShort_00`) |
| `0x7101e13258`, `0x7101e14b10`, `0x7101e13d40` | MessageShuffler | | Variant selection by message attributes (`MessageShufflerTalker`, ATR1 lookups through `0x7101e11ef0`, which has its own copy of the label hash). Probably where `_V000…` variants of dialogue are picked |

The label hash loop is inlined into at least 13 functions (scan for `movz #0x492`), so hooking `Msbt_GetTextByLabel` may miss a few paths. Runtime logging showed it misses most Mii speech, which the MessageShuffler fetches by row (`docs/game-research.md` §4).

### Text expansion (template + MessageParam → finished line)

| Address | Our name | Signature (inferred) | Evidence |
|---|---|---|---|
| `0x71001508ac` | `Text_Expand` | `u32? (TagProcessor* proc, char16_t* dst, u32* outLen?, ?, u64 dstCap, const char16_t* src, u32 srcLen, u64 ?, u32 ?, Flags* ?, MessageParam* mp)` | Walks the UTF-16 template (`0x0E` tag start, `0x0F` tag end); handles `UGCText` (group 2, tag 0) itself (below); hands other tags to the processor's virtual dispatcher. Called directly from 5 sites, and its address is in the vtable at `0x71032173c0`, so text boxes probably call it virtually. **Preferred line hook:** at return, `dst` holds the finished line and `src`/`mp` are still at hand. **Hooked at runtime since plugin 0.1.1: works.** Returns the result of its final call (`bl 0x71001519dc` just before `ret`); the UI text setter `0x710045e7cc` uses it (`mov w25, w0` after the call at `0x710045e920`), apparently as the number of choice-menu options. A hook must return it (plugin 0.7.2; earlier hooks dropped it and menus showed one option). It is the mod's main hook: passing another template to the original call changes balloon, TTS and log together |
| `0x7102108b4c` | `TagProcessor::ctor` | `void (TagProcessor*, const char** debugName)` | Installs vtable `0x7103217348`. Object is ~0x14d8 bytes, with a 0x800-char UTF-16 work buffer at `+0x4d4` |
| `0x7100163768` | `TagProcessor::dtor` | | Pairs with the ctor at every expansion site |
| `0x710210cb64` | `TagProcessor::ProcessTag` | virtual, vtable slot `0x71032174e0` | Switches on tag group: 2 Replace → `0x7100785564`, 5 Player → `0x7102108ec8`, 200 Mii → `0x71007818cc`, 201 Grammar → `0x710210b740` |

Runtime callers of `Text_Expand` (plugin 0.1.1, 2026-10-08): `0x71001424b0` = spoken-line balloon text; `0x7100163478` = same line as TTS input (VoiceText VTML); `0x7100a2d90c` = some spoken lines again (probably the conversation log); `0x710045e924`, `0x710045e9cc`, `0x7100a2d864` = UI; `0x7100786d60` = house names; `0x7100781c80` = caption subject (Mii name). On-screen captions at `0x710045e9cc` have return address `0x7100643428` (caption window text setter) on their call stack; the log, result windows (`0x71017fa550`), dialogs (`0x710202235c`) and tutorials don't (2026-10-11, all logs; plugin 0.6.5 marks `caption: 1`).

Expansion pattern (e.g. `0x71018eeeb0`, the AI query `SetGeneralMessageToMessageParamQuery`; `0x7100789cc0`, `GetPairDetailRelationMessage`): `Msbt_GetTextByLabel` → `TagProcessor::ctor` → `Text_Expand(…, template, len, …, MessageParam)` → `TagProcessor::dtor`.

### MessageParam (partial)

| Offset | Type | Meaning |
|---|---|---|
| `+0x3b0` | `u32[4]` | Taught-word (UGC) ID per `UGCText` slot; `>= 300` = not set (then `Text_Expand` picks a random word via `0x710016d2c0`) |
| `+0x4ec` | `bool[4]` | Slot already resolved |
| `+0x674` | `u32` | Input to the random-word pick (a Mii or seed) |
| `+0x678` | `u8` | Copied to `proc+0x5c` at the start of expansion |

### Language

| Address | What |
|---|---|
| `0x710074ee0c` | `GetRegionLanguage()`: returns the current language index (192 callers). Uses the override at `*(u32*)0x710522d768` if set (probably from the dev file `Local/System/RegionLangConfig.txt`), else table `0x71029a4c90[lang*7 + region]` from the system region and language |
| `0x710319e910` | `const char* kLangCode[18]`: `0 JPja, 1 USen, 2 USes, 3 USfr, 4 USpt, 5 EUen, 6 EUes, 7 EUfr, 8 EUde, 9 EUit, 10 EUpt, 11 EUnl, 12 EUru, 13 KRko, 14 CNzh, 15 TWzh, 16 EUqa, 17 XBqb` |
| `0x71005022c0` | `msg::MessageLoadMgr` load: `kLangCode[index]` → `0x7100502524` builds `Mals/%s.%s.sarc` |

**The plugin calls `GetRegionLanguage()` and sends `kLangCode[index]`.** Confirmed at runtime (plugin 0.1.0, `lang: USen`).

### Text-to-speech

| Address | What |
|---|---|
| `0x7100652188` | `VoiceTextMgr` init: picks the voice database from `GetRegionLanguage()`. Only some indices have a voice (`< 4`, or bit set in `0xebe0`), so `USpt`/`EUpt`/`EUru`/`EUnl` fall back to index 0 |
| `0x7100652f18` | Creates `VoiceTextSynthesizer`, `VoiceAnalyzer`, `VoiceEffector` |
| `0x7100f5….`–`0x7100f8….` | The VoiceText engine itself (statically linked: text normalizer, `<say-as>`, `<vt_pause>` handling) |
| `0x7100f59f30` | Engine text entry point: `(const char* text, int len /* -1 = strlen */, void* out, u32* outLen, int copy, …)`; converts the text through the engine's normalizer. Called from game code at `0x71004709b8` (inside a function Ghidra hasn't delimited): **lead for the TTS hand-off** |
| `MessageVoiceMgr` | `getName` `0x7102548648`, vtable from `0x7103268c48`. Its update method (`0x7102546f28`) manages named sound playback; probably not the TTS hand-off |
| `Text_Expand` caller `0x7100163478` | **The TTS hand-off (runtime, 2026-10-08):** each spoken line is expanded a second time from this caller, into VoiceText markup (`<vtml_sub>`, `<vtml_break>`, `<vtml_mark>`). Swapping the template at `Text_Expand` therefore changes the voice too |

### AI node classes (vtables)

The binary has no RTTI. Each AI node class has a 3-instruction `getName()` (`adrp`/`add`/`ret`) in its vtable; finding that method's pointer finds the vtable.

| Node | `getName()` | vtable slot holding it |
|---|---|---|
| `MiiTouchTalkAction` | `0x71017f9d30` | `0x71030c7968` (vtable starts `0x71030c78d8`) |
| `TalkMessageLabelAction` | `0x71018387e0` | `0x71030e1bd8` |
| `MiiTouchWaitSecServiceWaitSelector` | `0x710192528c` | `0x7103149cc8` |
| `ScenarioChatSelector` | `0x710192d910` | `0x710314da88` |
| `SetGroupFocusTelopSelector` | `0x7101930850` | `0x710314ece8` |

### ScenarioChatSelector internals (static, 2026-10-09)

vtable starts `0x710314d9f8` (two zero words before it). Class-specific slots (others are shared with `SetGroupFocusTelopSelector`): 0–3, 18 (`getName`), 19, 27, 40, 41, 43, 44.

| Address | What | Notes |
|---|---|---|
| `0x710192c53c` | slot 40: pick the chat and write outputs | Returns 16 bytes (`x1` = 0 on success, 1 on failure). Inputs (blackboard fields, tagged pointers, low 2 bits are flags): `+0x158` Mii list, `+0x160` list, `+0x168` sub type, `+0x170` type (copied to `+0x6c`/`+0x68`), `+0x178`/`+0x180`/`+0x188` ints (item type range-checked < 23; item id; word index, to confirm), `+0x190` (FocusTelop) and `+0x198` (Talk) slots holding `MessageParam*`. Picks an entry at random (xorshift128) into `+0x80`, copies MessageParam `+0x190` → `+0x198` (`0x71004bb350`, MessageParam copy; word IDs at `+0x3b0`). Signature `d10283ff fd0023ea f90037f9 6d04a3e9`. Plugin 0.1.5 hooked it (observation only): **did not fire** during a FocusLeadingChat chat (2026-10-09) |
| `0x710192d4c4` | slot 27: bind properties by name | `+0x158` Prop_Mii0List, `+0x160` Prop_Mii1List, `+0x168` ScenarioChatSubType, `+0x170` ScenarioChatType, `+0x178` ItemType, `+0x180` ItemId, `+0x188` UgcTextIndex, `+0x190` FocusTelopMessageParam, `+0x198` TalkMessageParam, `+0x1a0` TalkerList, `+0x1a8` ListenerList, `+0x1b0` ChatMessageLabel, `+0x1b8` MessageLabelRepeatNum, `+0x1c0` BalloonEffectKey, `+0x1c8`…`+0x1e8` balloon texture / IsCalc* flags; child indices `+0x1f0` Talk, `+0x1f1` Reaction, `+0x1f2` LastReaction, `+0x1f3` LastListenerTalk1, `+0x1f4` LastListenerTalk2 |
| `0x710192ce74` | slot 43: per-step child selection | Current child at `+0x1e` (0xff = none); turn counter `+0x84` vs `+0x88`. Signature `a9bd7bfd f9000bf5 a9024ff4 910003fd` |
| `0x710082f9f0` | SetGroupFocusTelopSelector slot 40 (enter) | Shows a caption: mstxt `**(node+0x70)`, label `*(node+0x88 & ~3)`, MessageParam slot `node+0x80`. Signature `a9bd7bfd f9000bfc 910003fd a9024ff4` |
| `0x710192cd14` | write outputs | `+0x1a0` TalkerList, `+0x1a8` ListenerList, `+0x1b0` ChatMessageLabel = one of `kChatMood[4]` at `0x71031a0dc0` (`ChatMiiNormal`, `ChatMiiHappy`, `ChatMiiBoredom`, `ChatMiiFight`), `+0x1b8` repeat count, `+0x1c0` balloon style from `0x71031a0d88` (`BVIconTalkBalloon_S/M/L/Replace_S/M/L/Loud`) |
| `0x7100778128`, `0x7100404380`, `0x7100880d90` | blackboard setters (string, int, pointer) | Write through the tagged pointer |
| `0x71052306e0` | "unbound property" sentinel | |

### Trouble "help me befriend" conversations (TroubleFriendUgc, static, 2026-10-09)

| Address | What | Notes |
|---|---|---|
| `0x71018c4048` | `GetTroubleFriendUgcTalkIndexRandomQuery`, vtable slot 10 (vtable around `0x7103120180`, `getName` `0x71018c4404`) | Bound in slot 9 (`0x71018c4274`): `+0x30` ActorHandle, `+0x38` UgcTextGenre, `+0x40` UgcTextAttribute, `+0x48` ForkStrMain, outputs `+0x50` DemoForkStrMain, `+0x58` DemoForkStrSub1, `+0x60` DemoForkStrSub2. Copies genre to `+0x28`, fork to `+0x2c`; picks the pattern with `0x7101ccd710`. Signature `d10203ff a9057bfd f90033f5 a9074ff4`. Plugin 0.1.7 hooks it (observation): **never produced an event** in 59 session logs to 2026-10-11, though befriend conversations played |
| `0x71031202e0` | `kForkStr[4]` | `Success`, `Failure`, `FailureSuccess`, `FailureFailure` |
| `0x71030730c8` | `kUgcGenre[4]` | `Person`, `Object`, `Action`, `Other` |
| `0x7103166f30` | `kOutcome[3]` (index outcome+1) | `Invalid`, `Success`, `Failure` |
| `0x7101a0a880` | line label builder `(self, StrBuf* {char* p; s32 cap}, genre, outcome, pattern, line)` | Called from `0x7101a0a3b4` ← TroubleFriendUgcTalkSelector `0x7101938738` (line counter `+0x188`). Signature `d100c3ff a9027bfd 910083fd 293f13a5`. Plugin 0.1.7 hooks it (observation): **never produced an event** either |
| `0x7101a0aca4` | result line label builder | `_Success_00`, `_Success_01` (or `FriendUgc_Common_Success_01`), `_Failure` |
| `0x7100399afc` | `snprintf`-style formatter for `FriendUgc_%s_%s_%04d_%02d` | Also used by the boot preload `0x7100399258` |
