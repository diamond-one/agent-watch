# Agent Watch: creative brief

A small always-on-top panel that tells you what your AI coding agents are doing, so you can stay in your own work and glance over only when something needs you.

**Audience:** people running several coding agents at once, on a desktop, mid-task.
**Goal:** less window-switching. You should know "running, done or needs me" in under a second.

## Direction

| Axis | Position | Why |
|---|---|---|
| Tone register | Professional | Instrument-panel voice. Short labels, no jokes, no exclamation marks. |
| Aesthetic philosophy | Editorial Restrained | Graphite surfaces, one accent colour per status, generous spacing, nothing decorative. |
| Audience relationship | Companion | It sits beside the work and speaks up only when it matters. It never nags. |
| Sensory ambition | Considered | The craft should be noticed in the details (switches, spacing, motion), never be the point. |

**Synthesis.** This brief produces a quiet monitor. Colour is information, not decoration: lime means working, amber means it needs you, green means done. Every control looks like what it does. The panel gets out of the way when idle, opens briefly when something happens, and never covers the taskbar.

## Settings UI/UX review

| # | Finding | Applied change |
|---|---|---|
| 1 | Nine controls of mixed types in one flat list, no hierarchy. | Two titled sections: **Alerts** and **Window**, with hairline dividers. |
| 2 | Toggles were "On"/"Off" text chips; state was ambiguous. | Real switches (track + knob). Green is on. |
| 3 | Sound picker was a "Chime ↻" button cycling blindly. | Segmented control showing every sound; tapping one previews it. |
| 4 | Position was six arrow glyphs with no spatial meaning. | A miniature screen with six dots, the taskbar drawn in, current spot lit. |
| 5 | "Stay open for" stayed live when the peek was off. | Dependent controls (peek length, alert sound) dim and disable when their parent is off. |
| 6 | The only way out of settings was to find the cog again. | Explicit **Done** button beside a "Settings" title. |
| 7 | Long labels ("Sound when an agent needs you" competing with controls). | Plain sentences, controls right-aligned, one control per row. |
| 8 | A permanent hint paragraph under Position. | Hint only sits beside the position map, and changes when you have a custom spot. |

## Header controls

- The collapse caret was a 9pt glyph with a much larger invisible hit area. Replaced with a real 28px icon button: the drawn surface is the clickable area, with hover and active states.
- The cog matches it in size and style.
- The chevron points the way the panel will open: **up** when docked to the bottom, **down** when docked to the top, and flips when open.

## Placement rules

- The panel grows away from the nearest screen edge. At the bottom it expands **upward**; it never drops over the taskbar.
- Position is always computed against the usable work area (taskbar excluded), per monitor.
- A custom (dragged) spot remembers which edges are nearest, so collapsing from settings returns to the same edge.

## Card content hierarchy

What a person wants from a card, most important first:

1. **Which agent** — its session title, as named in the app. Folder name is secondary.
2. **What it's doing or did** — in the agent's own words. **NOW** while running, **RESULT** when done. Never a raw tool name.
3. **What you asked** — the quoted prompt, small and quiet. It's context, not news.
4. **Raw activity** — tool steps, one click away behind "steps". Internal plumbing tools are hidden.

Narration is only shown if it was said during the current turn; anything older is dropped rather than shown as if it were current.

## Copy rules

- Plain sentences that say what the control does. "Jump to the chat when an agent finishes", not "Force focus".
- When a setting has a cost, say it once in a quiet helper line: "Takes focus from whatever you're working on."
- Labels are nouns or short verbs. Status words are upper-case small type (NOW, RESULT, DONE).
- No tool jargon, ids or file paths in the default view.

## Rejection list

No emoji as UI. No text labels pretending to be switches. No decorative gradients or shadows. No sound or animation that isn't tied to an event. No control that looks active when it does nothing.
