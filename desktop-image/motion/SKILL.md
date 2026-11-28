---
name: hyperframes
description: Read first to make, edit, animate or render any video with HyperFrames (HTML rendered to video), including launch films, explainers, music videos, charts, maps, vertical clips, title cards, captions and overlays. Holds the render contract, the work loop and what each kind of video needs.
---

**Environment:** the CLI is the global `hyperframes` command, run in the project folder. There is no network: do not fetch anything, and nothing here needs an account or a sign-in. Render with `/opt/motion/bin/render-project <project> <out.mp4>` (see "Render" below). Playbook files are under `/opt/motion/references/`; the one ready-made chart block is `/opt/motion/blocks/data-chart/`.


# HyperFrames

HyperFrames renders HTML to video by seeking a paused timeline frame by frame, so every render is identical; the person asks for changes and you edit the same files. Use it unless another framework or a plain browser recording is asked for.

`index.html` is the root composition: it declares the video's variables and mounts one scene per sub-composition from `compositions/`. Media and fonts live in `assets/`. A new project: `hyperframes init <dir> --skip-skills`.

Build what was asked: no voiceover or captions and no new aspect ratio unless the request calls for them. A new video is 1920×1080 at 30 fps. A question, a hold or a loose idea gets an answer or a proposal, not an edit. Before hand-writing a chart, check `/opt/motion/blocks/` for a ready-made one. No footage, voice or music is available here: build from text, shapes, the person's own files and the bundled fonts. Show real things from their source (the brand's logo, the person's own images); draw only what has none.

## Contract

`hyperframes check --json` reports most violations with a `Fix:` line.

1. Every timed element has `class="clip"`, `data-start` and `data-duration` in seconds, and shows over `[start, start + duration)`: land an end state just before its end. `data-track-index` is only a Studio lane (clips that never overlap share one: `references/format.md`); paint order is `z-index`.
2. The video's length is the root's `data-duration`: overrun is cut, an early end holds the last frame.
3. Each composition builds one paused timeline and registers it after its tweens: `window.__timelines["<data-composition-id>"] = tl`.
4. No `Date.now()`, unseeded `Math.random()`, network fetches, timers or `repeat: -1`. Everything visible is a function of timeline time.
5. Never tween `display`, `visibility` or `autoAlpha` on a `.clip`; animate a child.
6. No CSS `transform` on an element GSAP transforms; animate transforms, not `left`/`top`/`width`/`height`.
7. Never `play()`, `pause()` or seek media. A `<video>` keeps its sound with `data-has-audio="true"`, else it is `muted`; music, voice, replacement sound and J/L cuts are a separate `<audio>`.
8. Every `<audio>` has an `id` or it is left out of the mix. No `crossorigin` on media. Time the `<video>`, never its wrapper.
9. Only the bundled fonts (`references/format.md`) resolve by name; any other family needs `@font-face` to a file in `assets/fonts/`.
10. Asset paths: `assets/...` (no leading `/`), in sub-compositions too.
11. No CSS filters (grades, grain, blur, vignettes) on footage; there is no media-treatment tool here, so leave footage ungraded.
12. A scene file puts its styles, markup and scripts inside `<template>` and styles its root as `#root`. Intended overflow or overlap: `data-layout-allow-overflow`, `data-layout-allow-overlap`.

## Keep it editable

Keep the project easy to change later, by hand or by you.

- One scene per sub-composition, short readable ids; the root holds only scene hosts, media and audio.
- Timed elements are written in the HTML, not made by script, each with a stable `id`.
- Copy, brand colours and swappable media are variables (`data-var-text`, `data-var-src`, `var(--id)`).
- One kind per track: `data-track-kind="graphics"` on scene and overlay hosts, `captions` on the one host that holds every caption group.
- Content stays inside the 90% action-safe box, captions and key text inside 80% title-safe; never draw guides into the video.

## Work loop

- An existing project: `hyperframes timeline --json` shows what plays when; read it before any change.
- A new film over ~15 s that tells something: read its type's file (below), then write `SPEC.md` (`references/spec.md`): every adjective as a number, the defaults banned by name, the shots by frame.
- After your last edit: `hyperframes check --json`; fix every error, weigh every warning. A film: `hyperframes snapshot --review` and `references/review.md`, ending in `REVIEW.md`. A small edit: snapshot what you changed (`--at`, `--range`, `--composition`) and look before you reply.
- Render after your check and review pass (below), unless the person asked for no render.

## Render

`/opt/motion/bin/render-project <project-dir> <out.mp4> [max-seconds=600] [max-mb=200]` runs `check`, then starts the render detached with a time and size limit. It prints the done-file path (`<project-dir>/.render.done`) and returns at once; poll that file (it holds the exit code: 0 ok, 124 time limit, 90 size cap, other = render failed, read `<project-dir>/.render.log`). Do not run `hyperframes render` directly in a command that has its own timeout: the render would be killed with it.

## What to read

| The request | Read |
| --- | --- |
| Another film's style on a new topic | `references/look.md` |
| Explainer, tutorial | `references/types/explainer.md` |
| Chart, numbers | `references/types/data.md`; the block at `/opt/motion/blocks/data-chart/` |
| Vertical or social clip | `references/types/vertical.md` |
| Captions; cards over a talking head | `references/captions.md`; `references/overlays.md` |
| Motion: eases, titles, cuts, punch-ins, speed, holds | `references/motion.md` |
| Scenes, attributes, media, lint; GSAP, Lottie, 3D, canvas | `references/format.md` |
