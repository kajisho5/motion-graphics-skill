# Architecture

## Position in the ecosystem

```
video-production-agent
  Observation -> Event/Context -> Inference -> Policy/Preference/Constraint
  -> Decision -> ProductionPlan -> Project IR -> Compiler
                                                    |
                                                    v
                                          motion-graphics-skill
                                            typed request
                                            -> graphics validation
                                            -> timeline validation
                                            -> animation validation
                                            -> deterministic rendering
                                            -> output validation
                                            -> structured response
                                            -> provenance
                                                    |
                                                    v
                                              ffmpeg-skill
                                          (graphics.py, overlay.py, probe.py)
```

`video-production-agent` (or any caller) makes every design and timing decision and hands this Skill a fully
specified, typed Graphics Document. This Skill's only job is to render exactly that specification, deterministically,
and report what it did. It has no opinion about content.

## Modules

| Module | Responsibility |
|---|---|
| `model.py` | Typed Graphics Document / Element / Animation, structural + timeline validation, forbidden-field rejection. No file-system access. |
| `fonts.py` | Closed font registry + PathPolicy-checked custom font files. |
| `security.py` | `PathPolicy`: workspace confinement, allowed input roots, symlink-escape resolution, safe file names. |
| `canonical.py` | Canonical JSON + sha256 for every identity. |
| `adapter.py` | The only module that starts a subprocess. Locates and calls `ffmpeg-skill/{probe,graphics,overlay}.py` with typed argv. |
| `executor.py` | Request -> render plan -> per-element ffmpeg-skill call -> output validation -> structured response + provenance. Reuse/idempotency of non-final stages. |
| `contract.py` | `motion-graphics skill --json` — generated from the same tables `executor.py`/`model.py` run on. |
| `doctor.py` | `motion-graphics doctor --json` — environment vs. contract, three-valued capability status. |
| `errors.py` | The 13-code structured error model. |
| `cli.py` | argparse entry points: `skill`/`contract`, `doctor`, `validate`, `plan`, `run`. |

## Data flow for `run`

1. `cli.py` reads a request document (stdin or a file), parses argv, sets up signal handling.
2. `model.parse_request()` validates structure, forbidden fields, and the timeline — no file-system access.
3. `executor.Executor.response()`:
   - Resolves `video`/`output` paths through `PathPolicy`.
   - Probes the source video (`ffmpeg-skill/probe`) for resolution/duration.
   - Rejects any element whose `end` exceeds the real duration.
   - Resolves every image asset and font referenced by any element (PathPolicy + font registry), computing their
     sha256 up front — a missing asset fails before anything renders.
   - Computes a deterministic identity per element (previous stage + type + parameters with paths replaced by
     content hashes + tool versions), in canonical `(start, id)` order.
   - For every element but the last: reuses a cached intermediate when its manifest + sha256 match, otherwise
     calls `ffmpeg-skill/graphics` or `ffmpeg-skill/overlay` and validates the result (exists, non-empty, has a
     video stream, unchanged resolution, expected duration).
   - The last element always (re)renders directly into the requested output path and is always re-validated.
4. Returns one JSON response document: `output`, `timeline`, `operations`, `reused`, `engine`, `provenance`.

## What this Skill explicitly does not do (STEP 24)

AI/LLM reasoning, automatic design/animation/template selection, image understanding, face/scene detection,
semantic editing, transcription, subtitle generation/translation, QC decisions, automatic repair, cloud upload,
MCP, a plugin loader, or anything involving an arbitrary shell, ffmpeg filter, or JavaScript/HTML/CSS execution.
