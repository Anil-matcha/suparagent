# Changes to the vendored HyperFrames playbook

Source: `heygen-com/hyperframes` commit `4276039` (v0.9.104 era, 2026-11-27), Apache License 2.0 (`LICENSE.hyperframes`, copyright 2026 HeyGen, Inc.).
Copied files and where they sit in the image:

| Upstream | Here (`/opt/motion/...`) |
|---|---|
| `skills/hyperframes/SKILL.md` | `SKILL.md` |
| `skills/hyperframes/references/{format,motion,look,captions,overlays,review,spec}.md` | `references/` |
| `skills/hyperframes/references/types/{data,explainer,vertical}.md` | `references/types/` |
| `registry/blocks/data-chart/{data-chart.html,registry-item.json}` | `blocks/data-chart/` (unchanged) |
| `LICENSE` | `LICENSE.hyperframes` |

Not copied (and every pointer to them removed): `skills/hyperframes-cli`, `hyperframes-registry`, `media-use`, `reverse-any-video`, `style-sampling`, `slideshow`, `remotion-to-hyperframes`, `figma`; `references/{film,story,sound,runtimes,plugin-installation,skill-lifecycle,storyboard,storyboard-format,path-tracing}.md`; `references/types/{launch,music,3d,character}.md`; `assets/frame-presets`; `scripts/`.

Rule for every edit: no account, no sign-in, no network, no data sent anywhere. To update, re-copy from upstream at the new commit and re-apply this list.

## Global

- Every `npx hyperframes <command>` became `hyperframes <command>` (the CLI is installed globally in the image). All files.

## `SKILL.md` (upstream line numbers)

| Upstream | Change | Why |
|---|---|---|
| 6 | "Running the CLI" (plugin-cli launcher wrapper) replaced by an "Environment" paragraph | The launcher exists for plugin installs; also names `/opt/motion` paths and offline rule |
| 8 | Usage note and `events --skill=hyperframes` removed | Sends a usage event to HeyGen, linked to an account |
| 12 | `usage --json` plan check and `/hyperframes-cli` pointer removed; "edits ... in the Studio preview" reworded | Reads a HeyGen plan; skill not shipped |
| 14 | `init ... --skill=hyperframes` flag dropped; `capture <url>` sentence removed | `--skill` tags usage; `capture` loads a live website |
| 16 | Music/sound rule, `references/sound.md`, `/hyperframes-registry`, `/media-use` (HeyGen media) removed; points at `/opt/motion/blocks/` | HeyGen media and unshipped files |
| 32 (rule 11) | `media-treatment` / `/media-use` replaced by "no CSS filters on footage" | `media-use` not shipped. The 12 rules are kept |
| 37, 43 | "Studio edits the tags you write" and "Studio draws the guides" reworded | No Studio here |
| 47 | `history --since mine --who` and `/hyperframes-cli` removed (kept `timeline --json`) | Needs the CLI skill's protocol |
| 48 | Three-storylines step and `references/story.md` removed (kept `SPEC.md`) | File not shipped |
| 50 | "Render when the person approves the Studio preview" replaced | No Studio preview |
| 52-58 | Whole "HyperFrames desktop app" section (Framey, `hyperframes open`, `catch-up`) replaced by a "Render" section describing `bin/render-project` | Promotion of HeyGen's app |
| 64-65, 67, 69, 72-74, 77 (partly), 79 | Table rows and the closing line for `/reverse-any-video`, `/style-sampling`, launch, music, 3D, character, `film.md`, `runtimes.md`, `/slideshow`, `/remotion-to-hyperframes`, `/figma`, `skill-lifecycle.md` removed | Not shipped |

## References

| Upstream | Change |
|---|---|
| `captions.md` 5-6, 7-8, 10, 15, 43 | Whisper `transcribe` (downloads a model), `/media-use` HeyGen voiceover, the transcript-quality notes that depend on it, `catalog --tag caption-style` (online registry) and `remove-background` removed; timings now come from the person or are spaced evenly |
| `look.md` 15-16, 19 | `capture <url>`, `figma tokens`, and "download the `.woff2`" removed |
| `look.md` 25-44 | The whole "Presets" section (needs `assets/frame-presets`, not shipped) removed |
| `motion.md` 26, 44, 63 | `types/character.md` pointer, `catalog --tag transition` and `catalog --tag camera` lines removed (`catalog` reads the online registry) |
| `review.md` 25-26, 40 | `story.md` pointers replaced; the sub-agent / `STORYBOARD.md` paragraph removed |
| `spec.md` 7 | `story.md` pointer removed |
| `types/data.md` 15 | `catalog --query chart` and `runtimes.md` replaced by the vendored `data-chart` block |
| `types/explainer.md` 3, 22 | `story.md`, `sound.md`, `film.md` pointers removed; "the voice is the clock" -> narration text |
| `format.md` 83 | `/media-use` pointer removed |
| `format.md` 106-107 | Google Fonts fetch and `~/.cache` note replaced by "no network, stay with listed weights, ship other fonts as files"; "cloud render" wording removed |

## Known leftovers (not changed)

- `blocks/data-chart/registry-item.json` keeps upstream's `$schema` and `preview` URLs on `heygen.com` / `heygen.ai`. They are metadata; nothing reads them at render time. Strip them if a no-HeyGen-string rule is wanted.
- The upstream CLI prints its own "Keep editing by chatting with Framey" line after a render and after `preview` (`packages/cli/src/utils/desktopApp.ts`). The skill no longer tells the agent to repeat it; the wrapper does not filter it.
- "Studio" still appears as the name of the upstream editor in `format.md`, `captions.md`, `look.md`, `motion.md` (track lanes, variables panel). Harmless text; relevant if Studio is embedded in phase 2.
- `hyperframes docs <topic>` (named in `format.md`) reads docs bundled with the CLI (`packages/cli/src/docs`); no network.
