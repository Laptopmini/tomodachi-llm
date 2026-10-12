# AI graphs and hook candidates

Game 1.0.4. Graphs come from `../dumps/1.0.4/romfs_extracted/Pack/AI.Product.100/AI/*.ainb`, converted to YAML with `tomo ainb extract` into `../dumps/1.0.4/ainb_yaml/` (outside the repo). This file only records graph structure and node names; nothing here has been tested in game yet.

## BGYML enum hashes

The u32 enum values in BGYML files (`ScenarioChatType`, `ChatMessageLabelType`, `ChatASType`, …) are **MurmurHash3 x86_32, seed 0**, over the plain UTF-8 name. Found 2026-10-08 by hashing every string in `main` plus every message label with ten candidate functions against the 2,442 distinct large u32 values in `romfs_text/**/*.bgyml.yml`: mmh3 matched 329 values; the others matched none (one xxh64 coincidence). Tool: `scripts/bgyml_hash.py`.

Decoded values from `ScenarioChat/ScenarioChatParam/*.bgyml`:

| Field | Values seen |
|---|---|
| `ChatMessageLabelType` | `ChatMiiHappy`, `ChatMiiNormal`, `ChatMiiBoredom` (the Mii-language message groups) |
| `ChatASType` (animation) | `Good`, `Normal`, `Boredom`, `NotUnderstand` |
| `ChatBalloonEffectType` | `S`, `M`, `L` (balloon size) |
| `ScenarioChatTalkerType` | `Mii0`, `Mii1` |
| `ScenarioChatFlowRatioType` | `B`, `C` (picks a flow by the param's `RatioWeightA/B/C`) |
| still unknown | `2482271722`, `669634247` |

## Mii-to-Mii chat: `ScenarioChatState.module`

Module inputs: `MiiList`, `ScenarioChatType`, `ScenarioChatSubType`, `FocusTelopMessageLabel` (string), `ItemType`, `ItemId`, `UgcTextIndex`. The caller picks the caption label and passes it in.

| Node | Kind | What it does | Mod relevance |
|---|---|---|---|
| `CreateMessageParamQuery` ×2 → `WriteMessageParamToBlackboardAction` | C++ | Makes two `MessageParam` objects, `FocusTelopMessageParam` and `TalkMessageParam` (the data the tag processor substitutes: word, nicknames…) | Context source |
| `ScenarioChatSelector` | C++ | Inputs: `ScenarioChatType`, `ScenarioChatSubType`, `UgcTextIndex`, `ItemId/Type`, both `MessageParam`s. Outputs: `TalkerList`, `ListenerList`, `ChatMessageLabel`, `MessageLabelRepeatNum`, balloon settings, `IsCalcFocusTelopNode`, `IsCalcFeelingChangeNode` | **Early-query candidate:** the first point where both Miis, the word index, the chat type and the caption label are all known |
| `SetGroupFocusTelopSelector` | C++ | Shows the caption. `FocusTelopContentMessageLabel` from the module input, `MessageParam` = `FocusTelopMessageParam`, `Prop_MstxtName = ProgramMsg/FocusTelop/FocusTelopContent_GroupAction_FocusLeadingChat1`, `Prop_IsShowTelopUntilDone = true` | **Caption line site.** Also used by 7 other graphs (`EmotionTelopChatTelop.module`, `ChaseAndRun`, `FlashlightChat`, `GroupWalking`, …) |
| `_LocalModule_fed6b9fdfaaf` (node 10) | sub-graph | Plays the Mii-language turns: `ChatMessageLabel`, `TalkerList`, `ListenerList`, `TopicAS = AI_ChatTalker_Talk` | Mii language; left alone by this mod |
| `_LocalModule_fed6b9fdfaaf` (node 21) | sub-graph | Same module with talker and listener swapped, `TopicAS = AI_ChatListener_LastComment`: the closing line (`ListenerLastTalkMessageLabel`, e.g. `ChatMiiLastWordHappy`) | **Closing-line site** |
| `_LocalModule_f9a629f961d1` (nodes 15, 16, 20) | sub-graph | Reaction beats between turns (`AI_ChatListener_Reaction`, `AI_ChatTalker_WaitLastReaction`, …) | **Hold candidate for the closing line:** node 16 (`WaitLastReaction`) runs just before the closing line |
| `ButtonTrigAndHoldCheckQuery` + `WaitUntilAction` | C++ | Waits for a button press (skip / advance) | `WaitUntilAction(Bool)` is a generic per-frame wait, used in 18 graphs |

The caption wait graphs `EmotionTelopChatWait*.module` show a caption, then loop with `WaitSecAction` (3–5 s, 8–10 s random) and `LoopTopNodeSimultaneousSuccess`. The caption is on screen for seconds while Miis babble, so a generated caption has slack, but the caption text is needed when it first appears.

## Mii talks to the player: `MiiTouchBalloonTalk.root`

| Node | Kind | What it does | Mod relevance |
|---|---|---|---|
| `MiiTouchBalloonTalkSelector` | C++ | Picks the line: `BalloonTalkType`, `BalloonTalkIndex`, `AnswerIndex` | **Early-query candidate (tap talk)**, if it runs before the turn-to-camera |
| `IsActorFrontSelector` → `TurnToPositionWithQuickAction.module` | | The Mii turns to face the camera | Natural lead time |
| `MiiTouchWaitSecServiceWaitSelector` + `StartAndStopAnimWhenNoOtherRequestDecorator(ASName = MiiTouch_ServiceWait)` | C++ | Waits a set number of seconds with an idle "service wait" animation | **Hold candidate for tap talk:** an existing idle-while-waiting state |
| `MiiTouchBalloonTalkChoiceAction` | C++ | Choice balloon (player answers) | |
| `MiiTouchTalk.module` (`MessageLabel`, `mstxt`, `MessageSource`, `MessageType = 1`, `PostWaitFrame = 10`) → `MiiTouchTalkAction` | C++ | Shows and speaks the line | **Tap-talk line site** |

## The talk actions in C++

Strings in `main` give the AI node class names; every one listed here exists exactly once. Line-producing actions and how many graphs use them:

| Node | Graphs |
|---|---|
| `TalkMessageLabelAction` | 79 (generic "this Mii says label X") |
| `TalkMessageLabelDetailAction` | 4 |
| `TalkTalkMessageAction` | 2 |
| `MiiTouchTalkAction` | 4 |
| `SetGroupFocusTelopSelector` | 8 |

All of them take a message label (plus an `mstxt` file name and a `MessageParam`), so they very likely converge on one "label + MessageParam → finished string" function. That function is the preferred single line hook for captions, closing lines and tap talk (found: `Text_Expand`, `re/offsets.md`).

## Text-to-speech

The TTS engine is VoiceText (ReadSpeaker): `main` contains its demo strings and inline markup tags `<vt_pause>`, `<vt_speed>`, `<vt_pitch>`, `<vt_volume>`, `<vt_break>`. So TTS takes text with markup, and the MSBT `Voice` tag group (`Speed`, `Pause`, emotion tags) is probably converted into these. A generated line should be plain text; the module or game adds the markup. The engine is statically linked into `main`, with one voice per language and a pronunciation dictionary per language (`VoiceText/userdict_eng.csv`, …). Addresses are in `re/offsets.md`.

## Language at runtime

- `main` imports `nn::oe::GetDesiredLanguage` and loads text with the format string `Mals/%s.%s.sarc` (language code, `Product.100`), so the code (e.g. `USen`) exists as a string at runtime.
- Related strings: `Local/System/RegionLangConfig.txt`, `GetRegionLanguageIDQuery`, `LastRegionLanguage`, `UgcTextRegionLanguage` (a taught word records the language it was typed in), `IsSamePlayerNameRegionLanguage`.
- Found (static, 2026-10-08): `GetRegionLanguage()` at `0x710074ee0c` plus the code table at `0x710319e910` (`re/offsets.md`). The module sends `kLangCode[GetRegionLanguage()]`.
