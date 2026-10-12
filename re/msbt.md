# Message data

Game 1.0.4, `USen`. Sources: `../dumps/1.0.4/romfs/Mals/USen.Product.100.sarc.zs`, converted with `tomo romfs extract --convert` and indexed with `scripts/msgindex.py` (25,667 messages). Text is quoted here only as short examples needed to explain the format.

## Layout

- One SARC per language: `Mals/<lang>.Product.100.sarc.zs`. The file version is still `100` in game 1.0.4 (the update didn't touch text).
- Inside: `project.msbp` plus `ProgramMsg/` (87 MSBTs, dialogue), `LayoutMsg/` (119, UI), `ReplaceMsg/` (55, word lists such as `Food_Name`, `Goods_Name`), `ReferenceMsg/` (1).
- MSBT v3, UTF-16LE, sections `LBL1 ATO1 ATR1 TSY1 TXT2`.
- **No `.msbf` (event flow) files exist anywhere in RomFS.** Conversations are driven by AI graphs (`.ainb`, 2,574 files in `Pack/AI.Product.100/AI/`) plus BGYML parameter files.

## Labels and variants

- A label's base form is the line's identity; `_V000`, `_V001`, … are interchangeable variants (e.g. `ChatMiiHappy`, `ChatMiiHappy_V000`).
- Scripted multi-Mii chats use `<Group>_<NNNNNN>_<turn>[_Vnnn]`: turn `_0` is the opener, `_1` the response, with variants for the response.
- Each message carries **selection attributes** (ATR1, ~40 per message), which the game uses to pick a line. Examples: `Character` (personality mask), `Feeling` (mood mask), `BondLevel`, `RelationMiiAToB`, `FightingMiiAandB`, `TimeRange`, `Location`, `Season`, `Habit`, `V000RandomWeight`, `V000Priority`, `SecToStartTalk`, `UgcTextGenreAttribute`. This is free, structured context for the LLM prompt if we can read it at hook time.

## Control tags (from `project.msbp`)

Tag = (group ID, index within group). Relevant groups:

| Group | ID | Tags we care about |
|---|---|---|
| System | 0 | `Ruby`, `Font`, `Scale`, `Color`, `PageBreak`, `Reference` |
| Replace | 2 | **`UGCText`** (index 0), `UGCFood`, `UGCGoods`, `UGCCloth`, `UGCMapObject`, …, `GeneralMessage`, `GeneralItem`, `Food`, `Goods`, `RandomSetUGCText`, `IsSetUGCText` |
| Player | 5 | `PlayerNicknameByMii`, `PlayerNicknameCommon` |
| Voice | 6 | `Speed`, `Pause`, `SoundEffect`, emotion tags (`EmotionShout`, `EmotionAnger`, …), probably TTS controls |
| Balloon | 8 | `BalloonStyle` |
| Etc | 9 | `AS` (animation), `Expression`, `HabitFace` |
| Mii | 200 | `Nickname`, `RelationMiiNickname`, `WordShortStart_Ex_USEU` / `WordShortEnd_Ex_USEU` (the Mii's personal catchphrase wrapper), `WordLong` |
| Grammar | 201 | `WordAttr`, `DefArt`, `IndefArt`, `Cap`, `Decap`, `Count`, `CountT` (singular/plural agreement) |

### The word slot: `UGCText` (group 2, tag 0)

UGC = words the player has taught the island. Parameters:

| Param | Type | Values |
|---|---|---|
| `slot` | u8 | 0 in almost every line (the conversation's chosen word) |
| `genre` | enum | `Person`, `Object`, `Action`, `Image`, `Topic`, `Phrase`, `ConditionalAny` |
| `attr` | enum | `Any`, `Neutral`, `Positive`, `Negative`, `PositiveOrNeutral`, `NegativeOrNeutral` |
| `set` | enum | `Must`, `Random`, `UseOnlyIfOn`, `RandomOnlyIfOff` |

Words carry grammar metadata used by `IndefArt`/`CountT`/`WordAttr` (`Count=Singular|Plural`, article class), so the game can write "a garlic fry" vs "garlic fries". Generated lines must either avoid relying on that, or receive the word's number from the hook.

(Note: tomo 1.9.0's MSBP YAML drops the first letter of some tag-parameter names, e.g. `lot` for `slot`. The `.msbt` tag text is correct.)

## Where Mii-to-Mii conversation lives

**Important for the plan:** ordinary Mii-to-Mii chat about a taught word is **not** written in English. It's built from:

1. **Mii-language turns:** `ChatMiiLanguage/ChatMiiLanguage.msbt` (`ChatMiiNormal`, `ChatMiiHappy`, `ChatMiiBoredom`, `ChatMiiFast`, `ChatMiiFight`, `ChatMiiNoTextChat*`; 253 messages). These are gibberish ("So, ya leeba jargle with a grambee…"), sometimes with the taught word or a nickname embedded.
2. **A closing English line from the listener:** `ChatMiiLastWord{Happy,SuperGood,Boredom,NotUnderstand}` ("Nice talking to you, <nickname>.", "…Yeah, I didn't get any of that.").
3. **A narrator caption (telop)** that says what they're talking about, with the word slot: `FocusTelop/FocusTelopContent_GroupAction_EmotionTelopChat.msbt` (1,044 messages, grouped by mood: `EmotionTelopChat_Fervor_V…`, `_Love_`, `_Laugh_`, `_Dark_`, `_Sad_`, `_Secret_`, …; e.g. "They're joking about using <IndefArt/><UGCText genre=Object/> incorrectly.").

The turn sequence comes from `ScenarioChat/ScenarioChatParam/*.bgyml` (`GoodLong`, `SuperGoodLong`, `BoredomLong`, `NotUnderstandLong`, …): a list of flows, each a list of turns (`ChatMessageLabelType`, `ChatASType`, `ChatBalloonEffectType`) plus `ListenerLastTalkMessageLabel`. The state machine is `AI/ScenarioChatState.module.ainb`, which takes `ScenarioChatType`, `ChatMessageLabel`, `UgcTextIndex`, `TalkerList`, `ListenerList` as inputs.

### Player-directed chat ("focus leading")

When the player steers a Mii, the choice comes from `FocusLeading/FocusLeadingChoiceParam/*.bgyml` (`LeadingChat`, `LeadingChatKnow`, `LeadingChatGroup`, `Praise`, `CheerUp`, `Confession`, `InviteRestaurantChat`, …). Each has weighted branches whose results include a `ScenarioChatType`. The captions for these are in:
- `FocusTelopContent_GroupAction_FocusLeadingChat1.msbt` (245): "<Mii> is going on about how anyone who's into <UGCText> has impeccable taste."
- `Demo/DemoFocusLeadingChat2.msbt` (144): `FocusLeadingChat2_MakeLaugh_…`, `_Praise_…`.

### Scripted English conversations

Real English, multi-Mii, with openers and responses:
- `DemoChat/HouseTable/HouseTable00.msbt` (+`_Response`): chats at the home dining table.
- `DemoChat/HouseEtc/HouseEtc00.msbt` (+`_Other`).
- `DemoChat/Restaurant/For2/{CoupleGood,CoupleBad,LoverCouple}`, `For234/{BadBoys,Cheerup,Fashionable,FashionableDream,Innocent,Robot,UgcFan}`.
- `Chat/GroupSequentialChat.msbt`: meeting someone new, resolving accidents.
- Each scene has a matching AI graph (`DemoChatHouseTable00.root.ainb`, `DemoChatRobot.root.ainb`, …).

### Mii → player

- `MiiTouch/BalloonTalk/*` (`OneDirection` 1,072, `CustomizedSel` 802, `History` 478, `AI` 115, …): what a Mii says when you tap it, e.g. "Do you like <Food>, <player>?". English, single speaker.
- `Trouble/TroubleAskUgcWord.msbt`: Miis asking the player to teach them a word (this is where UGC words come from).

## Hashed IDs

BGYML enums such as `ScenarioChatType`, `ConditionId` and `ChatMessageLabelType` are stored as u32 hashes (e.g. `1645779707` = `Good`). The hash is **MurmurHash3 x86_32, seed 0** over the plain name (found 2026-10-08; `scripts/bgyml_hash.py`, decoded values in `re/ai-graphs.md`).

## Open items

- ~~Identify the ID hash function.~~ MurmurHash3 x86_32, seed 0 (`re/ai-graphs.md`).
- Map `ScenarioChatType` values to flows (the `ScenarioChatParam` turn fields are decoded; the type → param-file mapping is still open).
- Find how the caption (telop) and the Mii-language turns are timed relative to each other. First pass in `re/ai-graphs.md`: the caption shows first and stays up while the Miis babble (`EmotionTelopChatWait*` loops with 3–10 s waits).
- ~~Confirm in play which files produce English Mii conversations.~~ Done 2026-10-09/10-11: befriend scripts (`TroubleFriendUgc`), also used for spontaneous topic chats (`docs/game-research.md` §5).
