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

## Scoring

| Action | Points |
| --- | --- |
| Reaching a new row further up | 10 |
| Landing in a coral cave | 100 |
| Filling all five caves | 500 |

The best score is saved in the browser (`localStorage`).
