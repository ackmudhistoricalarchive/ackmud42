# ACK!MUD Area File Specification (Derived from `src` loader/saver code)

This document specifies the **actual on-disk area format** used by ACK!MUD (`ackmud42`), based on how the game boots area files (`src/db.c`) and writes them (`src/areasave.c`).

It intentionally documents parser behavior (including quirks), not just the intended format.

---

## 1) File Discovery and Top-Level Structure

- Area filenames are read from `AREA_LIST` (`../area/area.lst`) during boot.
- For each filename, the engine opens `../area/<filename>` and repeatedly reads sections that begin with `#`.
- Valid section headers:
  - `#AREA`
  - `#HELPS`
  - `#MOBILES`
  - `#MOBPROGS`
  - `#OBJECTS`
  - `#RESETS`
  - `#ROOMS`
  - `#SHOPS`
  - `#SPECIALS`
  - `#OBJFUNS`
- End of an area file is `#$`.

### Practical ordering notes

- Loader accepts sections in any order (as long as each header is valid).
- Official saver writes in this order:
  1. `#AREA`
  2. `#HELPS`
  3. `#MOBILES`
  4. `#MOBPROGS`
  5. `#OBJECTS`
  6. `#ROOMS`
  7. `#SHOPS`
  8. `#SPECIALS`
  9. `#OBJFUNS`
  10. `#RESETS`
  11. `#$`

---

## 2) Core Parsing Rules (applies everywhere)

### Numbers (`fread_number`)

- Leading whitespace is skipped.
- Supports optional `+`/`-` sign.
- Supports `|` expression chaining (e.g. `1|2|3` parses as sum `6`).
- Non-digit after optional sign is fatal.

### Strings (`fread_string`)

- Leading whitespace is skipped.
- String terminator is `~`.
- Newline normalization: `\n` in file becomes `\n\r` in memory.
- A single `~` means empty string.

### “Read rest of line” (`fsave_to_eol`)

- Resets keep trailing text/comments in a `notes` field (including newline chars captured from source line).

### Words (`fread_word`)

- Unquoted words end at whitespace.
- Quoted words are supported by generic parser behavior.

---

## 3) `#AREA` Header Section

### Layout

```text
#AREA
<name>~
[optional lettered lines...]
```

The first line after `#AREA` is always the area display name string.

Then loader reads zero or more single-letter directives until it sees the next `#` (start of next section).

### Supported `#AREA` directives

- `F <reset_rate_number>`
  - Area reset interval.
- `O <owner_string>~`
  - Area owner.
- `U <reset_message_string>~`
  - Message sent to awake PCs before reset.
- `R <can_read_string>~`
  - Build permission read ACL string.
- `W <can_write_string>~`
  - Build permission write ACL string.
- `P <anything-to-eol>`
  - Sets `AREA_PAYAREA` flag.
- `M <anything-to-eol>`
  - Sets `AREA_NO_ROOM_AFF` flag.
- `X <offset_number>`
  - VNUM offset metadata.
- `V <min_vnum> <max_vnum>`
  - Declared vnum range for area.
- `N <area_num>`
  - Explicit area index.
- `T <anything-to-eol>`
  - Sets `AREA_TELEPORT` flag.
- `B <anything-to-eol>`
  - Sets `AREA_BUILDING` flag.
- `S <anything-to-eol>`
  - Sets `AREA_NOSHOW` flag.
- `K <keyword_string>~`
  - Internal area keyword.
- `L <level_label_string>~`
  - Label shown in area listings.
- `I <min_level> <max_level>`
  - Suggested level band.

### Defaults if directive omitted

- `reset_rate = 15`
- `owner = ""`
- `can_read = "all"`
- `can_write = "all"`
- `level_label = "{?? ??}"`
- `keyword = "none"`
- `reset_msg = "You hear the screams of the Dead within your head."`
- `min_level = max_level = 0`
- `min_vnum = 0`, `max_vnum = MAX_VNUM`
- `offset = 0`
- `area_num` auto-assigned if not provided (`N`).

---

## 4) `#HELPS` Section

### Layout

Repeated entries:

```text
#HELPS
<level> <keyword>~
<text>~
...
0 $~
```

- Terminator is `0 $~`.
- If help text begins with whitespace, saver prefixes a `.` before writing; loader just reads a normal string.

---

## 5) `#MOBILES` Section

### Per-mobile record

```text
#<vnum>
<player_name>~
<short_descr>~
<long_descr>~
<description>~
<act_flags> <affected_by_flags> <alignment> S
<level> <sex>
<ac_mod> <hr_mod> <dr_mod>
[optional "new fields" line]
[optional inline mobprog blocks]
```

Section terminator:

```text
#0
```

### Required details

- The line ending in `S` is strict; missing `S` is fatal.
- `level` is loaded with `number_fuzzy(...)` randomization.
- `ACT_IS_NPC` is force-added regardless of file flags.

### Optional “new fields” line

If next char is `!`, loader parses:

```text
! <class> <clan> <race> <position_ignored_on_load> <skills> <cast> <def>
```

Important behavior:

- Loader **reads but ignores** stored position value and forces NPC position to `POS_STANDING` at index load time.
- Saver still writes current `position` value.

### Inline mobprogs inside mobile

If next char is `>`, parser enters inline program mode:

- Program entries:
  - `>act_prog <arglist>~`
  - `>speech_prog <arglist>~`
  - `>rand_prog <arglist>~`
  - `>fight_prog <arglist>~`
  - `>hitprcnt_prog <arglist>~`
  - `>death_prog <arglist>~`
  - `>entry_prog <arglist>~`
  - `>greet_prog <arglist>~`
  - `>all_greet_prog <arglist>~`
  - `>give_prog <arglist>~`
  - `>bribe_prog <arglist>~`
  - `<comlist>~` follows each arglist.
- Terminator is `|` on a line.
- `>in_file_prog <filename>~` is also supported and causes nested load from `MOBProgs/<filename>`.

---

## 6) `#MOBPROGS` Section (external-file attachments)

This section binds mobs to external mobprog files.

### Layout

```text
#MOBPROGS
M <mob_vnum> <filename>
M <mob_vnum> <filename>
...
S
```

- Each `M` line calls `mprog_file_read(filename, mob)` and loads commands from `MOBProgs/<filename>`.
- `*` comment lines are allowed.
- Section ends with `S`.

### External file format (`MOBProgs/<filename>`)

- Entries are numeric-prog style:
  - `><type_number> <arglist>~`
  - `<comlist>~`
- End marker is `|`.
- `IN_FILE_PROG` nesting is rejected when already reading from file (prevents nested in-file loops).

---

## 7) `#OBJECTS` Section

### Per-object record

```text
#<vnum>
<name>~
<short_descr>~
<description>~
<item_type> <extra_flags> <wear_flags> <item_apply>
<value0> <value1> <value2> <value3>
<weight>
[zero or more A/E/L blocks]
```

Terminator:

```text
#0
```

### Optional sub-blocks

- Affect:
  ```text
  A
  <location> <modifier>
  ```
- Extra description:
  ```text
  E
  <keyword>~
  <description>~
  ```
- Level:
  ```text
  L
  <level>
  ```

### Object loader behavior worth knowing

- If `item_type == ITEM_POTION`, loader forcibly sets `ITEM_NODROP` in `extra_flags`.
- For spells in values:
  - `ITEM_PILL`, `ITEM_POTION`, `ITEM_SCROLL`: `value[1..3]` are read as slot numbers and translated via `slot_lookup`.
  - `ITEM_STAFF`, `ITEM_WAND`: `value[3]` translated via `slot_lookup`.
- Saver performs reverse translation back to slot IDs before writing.

---

## 8) `#ROOMS` Section

### Per-room record

```text
#<vnum>
<name>~
<description>~
<room_flags> <sector_type>
[zero or more D/E entries]
S
```

Section terminator:

```text
#0
```

### Exit entry (`D`)

```text
D<door>
<description>~
<keyword>~
<locks> <key_vnum> <to_room_vnum>
```

- `door` must be 0..5.
- Legacy compatibility:
  - if `locks == 2`, loader interprets as `EX_ISDOOR | EX_PICKPROOF` (old format special case).
  - otherwise loader stores `locks` directly as `exit_info` bitset.
- Saver strips `EX_CLOSED` and `EX_LOCKED` before writing `locks`, because those are runtime state handled by reset `D` commands.

### Room extra description (`E`)

```text
E
<keyword>~
<description>~
```

### Room section stop

- Each room ends with a single `S` line.
- Unknown record code inside room body is fatal.

---

## 9) `#SHOPS` Section

### Layout

```text
#SHOPS
<keeper_vnum> <buy_type1> <buy_type2> <buy_type3> <buy_type4> <buy_type5> <profit_buy> <profit_sell> <open_hour> <close_hour>
...
0
```

- Exactly `MAX_TRADE` (5) buy types are read.
- End marker is keeper `0`.
- Loader links shop to mob index `keeper` (`mob->pShop = shop`).

---

## 10) `#SPECIALS` Section

### Layout

```text
#SPECIALS
M <mob_vnum> <spec_fun_name>
...
S
```

- `*` comment lines allowed.
- `S` ends section.
- `spec_fun_name` is resolved via `spec_lookup`.

---

## 11) `#OBJFUNS` Section

### Layout

```text
#OBJFUNS
O <obj_vnum> <obj_fun_name>
...
S
```

- `*` comment lines allowed.
- `S` ends section.
- `obj_fun_name` is resolved via `obj_fun_lookup`.

---

## 12) `#RESETS` Section

### Line format

General parser form:

```text
<command> <ifflag> <arg1> <arg2> [arg3]
```

- For commands `G` and `R`, parser does **not** read `arg3` from numeric fields (it becomes `0`).
- Remainder of line is preserved in `reset->notes`.
- Section ends on command `S`.
- `*` comment lines are skipped.

### Command meanings at runtime (`reset_area`)

- `M if mob_vnum max_existing room_vnum`
  - Load mob in room if current count of that mob index is below `max_existing`.
- `O if obj_vnum max_existing room_vnum`
  - Load object into room, with additional room/object-type constraints.
- `P if obj_vnum limit container_obj_vnum`
  - Put object in object template instance.
- `G if obj_vnum limit`
  - Give object to last reset mob.
- `E if obj_vnum limit wear_loc`
  - Equip object on last reset mob at `wear_loc`.
- `D if room_vnum door state`
  - Door state: `0=open`, `1=closed`, `2=closed+locked`.
- `R if room_vnum max_dir`
  - Randomize exits `[0..max_dir-1]`.
- `A` exists but treated obsolete.

### Validation behavior (two-phase)

1. `load_resets` only keeps resets that can be associated with a current room context.
2. `check_resets` later validates vnum references and door constraints globally after all areas load, deleting invalid resets and logging bugs.

### Important quirk: `ifflag`

- `ifflag` is parsed/saved but currently unused in execution logic for reset conditions.

---

## 13) Section Terminators Summary

- `#AREA`: ends when next `#...` header is encountered.
- `#HELPS`: `0 $~`
- `#MOBILES`: `#0`
- `#MOBPROGS`: `S`
- `#OBJECTS`: `#0`
- `#ROOMS`: `#0` (with per-room `S`)
- `#SHOPS`: `0`
- `#SPECIALS`: `S`
- `#OBJFUNS`: `S`
- `#RESETS`: `S`
- File end: `#$`

---

## 14) Flags/Bits Directly Relevant to Area Files

### Area flags (`#AREA` directives)

- `AREA_PAYAREA` (via `P`)
- `AREA_TELEPORT` (via `T`)
- `AREA_BUILDING` (via `B`)
- `AREA_NOSHOW` (via `S`)
- `AREA_NO_ROOM_AFF` (via `M`)

### Exit bits (room `D` records and reset `D` behavior)

- `EX_ISDOOR`
- `EX_CLOSED`
- `EX_LOCKED`
- `EX_PICKPROOF`

---

## 15) Minimal Complete Example Skeleton

```text
#AREA
My Area Name~
K myarea~
L {10 20}~
N 12
I 10 20
V 1200 1299
F 15
U You feel reality shift around you.~

#MOBILES
#1200
guard city~
a city guard~
A city guard stands here.~
He looks alert.~
1 0 0 S
10 1
0 0 0
! 0 0 0 8 0 0 0
#0

#OBJECTS
#1201
sword iron~
an iron sword~
An iron sword is here.~
5 0 0 0
0 0 0 0
5
L
10
#0

#ROOMS
#1202
Gatehouse~
A sturdy gatehouse.~
0 0
D0
A gate leads north.~
gate~
1 0 1203
S
#0

#RESETS
M 0 1200 1 1202
E 0 1201 1 16
D 0 1202 0 1
S

#$
```

---

## 16) Deep-Behavior Notes for Builders/Tooling Authors

- Duplicate vnums in `#MOBILES`, `#OBJECTS`, `#ROOMS` are fatal during boot.
- `#RESETS` are forgiving at parse time but aggressively culled by `check_resets` later.
- Inline mobprogs and `#MOBPROGS` both contribute to mob program lists.
- Saving strips comments from `#SPECIALS` / `#OBJFUNS` (comment lines are not preserved).
- Area save writes to `<file>.new`, rotates old file to `<file>.old`, then renames new into place.
- Runtime reset messaging uses per-area `reset_msg` when players are in the area and reset is nearing.


---

## 17) Value Dictionaries (Deep Reference)

This section expands the numeric fields used in area files into their flag/enum meanings.

> Notes:
> - Canonical bit values come from `src/merc.h`.
> - Builder-facing names (typed in OLC) come from `src/buildtab.c` tables and are included where useful.

### 17.1 `#MOBILES` → `act_flags`

Bitmask values:

- `1` `ACT_IS_NPC` (`is_npc`)
- `2` `ACT_SENTINEL` (`sentinel`)
- `4` `ACT_SCAVENGER` (`scavenger`)
- `8` `ACT_REMEMBER` (`remember`)
- `16` `ACT_NO_FLEE` (`no_flee`)
- `32` `ACT_AGGRESSIVE` (`aggressive`)
- `64` `ACT_STAY_AREA` (`stay_area`)
- `128` `ACT_WIMPY` (`wimpy`)
- `256` `ACT_PET` (`pet`)
- `512` `ACT_TRAIN` (`train`)
- `1024` `ACT_PRACTICE` (`practice`)
- `2048` `ACT_MERCENARY` (`mercenary`)
- `4096` `ACT_HEAL` (`heal`)
- `8192` `ACT_ADAPT` (`adapt`)
- `16384` `ACT_UNDEAD` (`undead`)
- `32768` `ACT_BANKER` (`bank`)
- `65536` `ACT_NO_BODY` (`no_body`)
- `131072` `ACT_HUNTER` (`hunter`)
- `262144` `ACT_NOMIND` (`no_mind`)
- `524288` `ACT_POSTMAN` (`postman`)
- `1048576` `ACT_REWIELD` (`rewield`)
- `2097152` `ACT_RE_EQUIP` (`reequip`)
- `4194304` `ACT_INTELLIGENT` (`intelligent`)
- `8388608` `ACT_VAMPIRE` (`vampire`)
- `16777216` `ACT_BREEDER` (`breeder`)
- `33554432` `ACT_SOLO` (`solo`)
- `67108864` `ACT_WEREWOLF` (`werewolf`)
- `134217728` `ACT_MOUNT` (`mount`)
- `BIT_29` `ACT_NOBLOOD` (`no_blood`)

### 17.2 `#MOBILES` → `affected_by_flags`

Bitmask values:

- `1` `AFF_BLIND` (`blind`)
- `2` `AFF_INVISIBLE` (`invisible`)
- `4` `AFF_DETECT_EVIL` (`detect_evil`)
- `8` `AFF_DETECT_INVIS` (`detect_invis`)
- `16` `AFF_DETECT_MAGIC` (`detect_magic`)
- `32` `AFF_DETECT_HIDDEN` (`detect_hidden`)
- `64` `AFF_CLOAK_REFLECTION` (`cloak:reflection`)
- `128` `AFF_SANCTUARY` (`sanctuary`)
- `256` `AFF_FAERIE_FIRE` (`faerie_fire`)
- `512` `AFF_INFRARED` (`infrared`)
- `1024` `AFF_CURSE` (`curse`)
- `2048` `AFF_CLOAK_FLAMING` (`cloak:flaming`)
- `4096` `AFF_POISON` (`poison`)
- `8192` `AFF_PROTECT` (`protect`)
- `16384` `AFF_CLOAK_ABSORPTION` (`cloak:absorption`)
- `32768` `AFF_SNEAK` (`sneak`)
- `65536` `AFF_HIDE` (`hide`)
- `131072` `AFF_SLEEP` (`sleep`)
- `262144` `AFF_CHARM` (`charm`)
- `524288` `AFF_FLYING` (`flying`)
- `1048576` `AFF_PASS_DOOR` (`pass_door`)
- `2097152` `AFF_ANTI_MAGIC` (`anti_magic`)
- `4194304` `AFF_DETECT_UNDEAD` (`detect_undead`)
- `8388608` `AFF_BESERK` (`berserk`)
- `16777216` `AFF_VAMP_BITE`
- `33554432` `AFF_VAMP_HEALING` (commented as DO NOT USE in OLC)
- `67108864` `AFF_HOLD`
- `134217728` `AFF_PARALYSIS`
- `268435456` `AFF_CLOAK_ADEPT`
- `536870912` `AFF_CLOAK_REGEN`

### 17.3 `#MOBILES` scalar fields and extended `!` fields

- `sex`:
  - `0` neutral
  - `1` male
  - `2` female
- `! <class> <clan> <race> <position> <skills> <cast> <def>`:
  - `position` is read then ignored at index load (index position forced standing).
  - `skills`, `cast`, `def` are bitmasks.

`skills` (`tab_mob_skill` / `MOB_*`):

- `1` `MOB_NONE` (`nada`)
- `2` `MOB_SECOND` (`2_attack`)
- `4` `MOB_THIRD` (`3_attack`)
- `8` `MOB_FOURTH` (`4_attack`)
- `16` `MOB_PUNCH` (`punch`)
- `32` `MOB_HEADBUTT` (`headbutt`)
- `64` `MOB_KNEE` (`knee`)
- `128` `MOB_DISARM` (`disarm`)
- `256` `MOB_TRIP` (`trip`)
- `512` `MOB_NODISARM` (`nodisarm`)
- `1024` `MOB_NOTRIP` (`notrip`)
- `2048` `MOB_DODGE` (`dodge`)
- `4096` `MOB_PARRY` (`parry`)
- `8192` `MOB_MARTIAL` (`martial`)
- `16384` `MOB_ENHANCED` (`enhanced`)
- `32768` `MOB_DUALWIELD` (`dualwield`)
- `65536` `MOB_DIRT` (`dirt`)
- `131072` `MOB_FIFTH` (`5_attack`)
- `262144` `MOB_SIXTH` (`6_attack`)
- `524288` `MOB_CHARGE` (`charge`)

`cast` (`tab_mob_cast` / `CAST_*`):

- `1` `CAST_NONE` (`placeholder` in table)
- `2` magic missile
- `4` shocking grasp
- `8` burning hands
- `16` colour spray
- `32` fireball
- `64` hellspawn
- `128` acid blast
- `256` chain lightning
- `512` faerie fire (table token present)
- `1024` flare
- `2048` flamestrike
- `4096` earthquake
- `8192` mind flail
- `16384` planergy
- `32768` phobia
- `65536` mind bolt
- `131072` static
- `262144` ego whip
- `524288` bloody tears
- `1048576` mindflame
- `2097152` suffocate
- `4194304` nerve fire
- `8388608` light bolt
- `16777216` heat armor
- `33554432` lava burst

`def` (`tab_mob_def` / `DEF_*`):

- `1` `DEF_NONE`
- `2` cure light
- `4` cure serious
- `8` cure critic
- `16` heal
- `32` fireshield
- `64` iceshield
- `128` shockshield

### 17.4 `#OBJECTS` field dictionaries

`item_type` values:

- `1` light
- `2` scroll
- `3` wand
- `4` staff
- `5` weapon
- `6` beacon
- `7` portal
- `8` treasure
- `9` armor
- `10` potion
- `11` clutch
- `12` furniture
- `13` trash
- `14` trigger
- `15` container
- `16` quest
- `17` drink_con
- `18` key
- `19` food
- `20` money
- `21` stake
- `22` boat
- `23` corpse_npc
- `24` corpse_pc
- `25` fountain
- `26` pill
- `27` board
- `28` soul
- `29` piece
- `30` spell_matrix
- `31` enchantment

`extra_flags` bitmask:

- `1` glow
- `2` hum
- `4` dark (also aliased as `ITEM_NODISARM`)
- `8` lock
- `16` evil
- `32` invis
- `64` magic
- `128` nodrop
- `256` bless
- `512` anti_good
- `1024` anti_evil
- `2048` anti_neutral
- `4096` noremove
- `8192` inventory
- `16384` nosave
- `32768` clan_eq
- `65536` trig_destroy
- `131072` no_auction
- `262144` remort
- `524288` adept
- `1048576` rare
- `2097152` vamp
- `4194304` noloot
- `8388608` nosac
- `16777216` unique
- `BIT_26` lifestealer
- `BIT_27` silver

`wear_flags` bitmask:

- `1` take
- `2` finger
- `4` neck
- `8` body
- `16` head
- `32` legs
- `64` feet
- `128` hands
- `256` arms
- `512` shield
- `1024` about
- `2048` waist
- `4096` wrist
- `8192` wield
- `16384` hold
- `32768` face
- `65536` ear
- `131072` hold_magic / hold_clutch

`item_apply` bitmask:

- `1` none
- `2` infra
- `4` invis
- `8` det_invis
- `16` sanc
- `32` sneak
- `64` hide
- `128` prot
- `256` enhanced
- `512` det_mag
- `1024` det_hid
- `2048` det_evil
- `4096` pass_door
- `8192` det_poison
- `16384` fly
- `32768` know_align
- `65536` detect_undead
- `131072` heated

Object affect locations (`A` entries, `location`):

- `0` none
- `1` str
- `2` dex
- `3` int
- `4` wis
- `5` con
- `6` sex
- `7` class
- `8` level
- `9` age
- `10` height
- `11` weight
- `12` mana
- `13` hit
- `14` move
- `15` gold
- `16` exp
- `17` ac
- `18` hitroll
- `19` damroll
- `20` saving_para
- `21` saving_rod
- `22` saving_petri
- `23` saving_breath
- `24` saving_spell

Container flags (`value[1]` when `item_type=container`): closeable=1, pickproof=2, closed=4, locked=8.

### 17.5 `#ROOMS` field dictionaries

`room_flags` bitmask:

- `1` dark
- `2` regen
- `4` no_mob
- `8` indoors
- `16` no_magic
- `32` hot
- `64` cold
- `128` pk
- `256` quiet
- `512` private
- `1024` safe
- `2048` solitary
- `4096` pet_shop
- `8192` no_recall
- `16384` no_teleport
- `32768` hunt_hunt / hunt_mark (internal)
- `65536` no_bloodwalk
- `131072` no_portal
- `BIT_19` no_repop

`sector_type` enum:

- `0` inside
- `1` city
- `2` field
- `3` forest
- `4` hills
- `5` mountain
- `6` water_swim
- `7` water_noswim
- `8` recall_set (`SECT_RECALL_OK`)
- `9` air
- `10` desert
- `11` max/sentinel

Exit flags (`D` line `locks`/`exit_info`):

- `1` door (`EX_ISDOOR`)
- `2` closed (`EX_CLOSED`) - generally reset-managed
- `4` locked (`EX_LOCKED`) - generally reset-managed
- `8` climb
- `16` immortal
- `32` pickproof
- `64` smashproof
- `128` passproof
- `256` nodetect

Door state in reset `D` commands (`arg3`):

- `0` open
- `1` closed
- `2` locked

### 17.6 `#RESETS` `E` wear-location (`arg3`) dictionary

- `0` light
- `1` finger_l
- `2` finger_r
- `3` neck_1
- `4` neck_2
- `5` body
- `6` head
- `7` legs
- `8` feet
- `9` hands
- `10` arms
- `11` shield
- `12` about
- `13` waist
- `14` wrist_l
- `15` wrist_r
- `16` wield
- `17` hold
- `18` face
- `19` ear
- `20` clutch/magic-hold slot
- `21` wield_2

### 17.7 `#MOBPROGS` / inline mobprog types

Accepted textual program types in inline mobile records:

- `in_file_prog`
- `act_prog`
- `speech_prog`
- `rand_prog`
- `fight_prog`
- `hitprcnt_prog`
- `death_prog`
- `entry_prog`
- `greet_prog`
- `all_greet_prog`
- `give_prog`
- `bribe_prog`

Section-level `#MOBPROGS` links use lines of form: `M <mob_vnum> <filename>` and `S` terminator.
