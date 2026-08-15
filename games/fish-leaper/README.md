# 🐠 Fish Leaper

A Frogger-style ocean game for young kids. Help a little clownfish leap up the
screen, dodge everything in the water, and tuck safely into all five coral caves.

## How to play

Open `index.html` in any browser. That's it — no install, no build step, no
internet needed. One file, no libraries.

- **Move:** arrow keys / WASD, the big on-screen buttons, or swipe on a tablet
- **Tap the water** = one hop upward (handy for very small players)
- **Start / restart:** tap anywhere or press Space

## The goal

Fill all 5 coral caves at the top of the screen. Fill them all and you level up
to a slightly faster ocean — and get a bonus starfish life.

## What's in the water

| Critter | Where | What it does |
| --- | --- | --- |
| 🪼 Jellyfish | upper ocean | drifts slowly, bumps you |
| 🦈 Shark | upper ocean | fast! the big one to watch |
| 🟣 Sea urchin | upper ocean | spiky and slow |
| 🦀 Crab | lower ocean | scuttles sideways |
| 🪤 Fishing net | lower ocean | floats along, catches fish |

Two rows are always safe: the **kelp bed** in the middle and the **sandy floor**
at the bottom. Rest there as long as you like.

## Made kid-friendly on purpose

- **No timer.** Nothing rushes you.
- **5 starfish lives**, plus an extra one each time you finish a level.
- **Forgiving hitboxes** — the fish is smaller than it looks, the critters are
  bigger than their collision box.
- **Missing a cave costs nothing.** The fish just bounces back to the start with
  a "try there!" nudge instead of losing a life.
- **You are never invisible** — after a bump the fish shimmers instead of
  blinking out, so little players can always see where they are.
- **Speed caps out** at 1.75× no matter how high the level gets.
- Cheerful sounds you can turn off with the 🔊 button, big chunky art, and
  encouraging messages ("Yay!", "Woohoo!") every time you land a cave.

## Soundtrack

The music is a little NES sound chip built out of WebAudio — no audio files are
shipped, every note is generated as you play:

| Channel | Voice | Job |
| --- | --- | --- |
| Pulse 1 | 25% duty square | lead melody |
| Pulse 2 | 12.5% duty square | chord arpeggios |
| Triangle | triangle wave | bass line |
| Noise | filtered white noise | kick, snare, hats |

Three original themes:

| Theme | When | Feel |
| --- | --- | --- |
| Title | title screen | 4 bars, 122 BPM, C major |
| **Reef Party** | gameplay (default) | 8 bars, 152 BPM, C - Am - F - G, drum fill on the turnaround |
| **Deep Water** | gameplay (press 🎵) | 8 bars, 160 BPM, E minor riff, chugging bass and double kick |

The 🎵 button under the speaker switches the gameplay theme and remembers your
pick. Deep Water is the heavy one: staccato low-E chug on the triangle channel,
root/fifth 16ths on pulse 2 to fake a power chord the way NES games did, and a
beat that ends each bar on a double kick.

Tempo climbs 5 BPM per level (capped at 184), so the ocean feels busier as you
get further without the music changing key.

Patterns live in the `SONGS` object as 16 steps per bar, where `.` means
"nothing on this step" — so a bar of melody is just a readable string:

```
"G4 . A4 . C5 . . . C5 . A4 . G4 . . ."
```

A look-ahead scheduler queues notes against the audio clock, so timing stays
tight even when the frame rate wobbles. The 🔊 button mutes music and effects
together, and the chip goes quiet when the tab is in the background.

## Scoring

| Action | Points |
| --- | --- |
| Reaching a new row further up | 10 |
| Landing in a coral cave | 100 |
| Filling all five caves | 500 |

The best score is saved in the browser (`localStorage`).
