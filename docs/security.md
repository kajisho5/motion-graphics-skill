# Security boundary

| Threat | Enforced by | Test |
|---|---|---|
| Shell injection | `subprocess.Popen` with an argv list, never `shell=True`, in exactly one place (`adapter.FfmpegSkill._popen`). | `test_security.py::test_no_shell_true` |
| Arbitrary executable | Only `sys.executable` and `ffmpeg-skill/{probe,graphics,overlay}.py` (via `FfmpegSkill.script`, allowlisted by `TOOLS_USED`) are ever invoked. | `test_security.py::test_tool_allowlist` |
| Arbitrary ffmpeg filter / `filter_complex` / `-vf` / `-af` | This Skill never constructs a filter string; every visual effect is one ffmpeg-skill CLI flag built from a validated number, a closed-vocabulary string, or a resolved path. `filter`/`filters`/`filter_complex`/`vf`/`af` are also rejected as request field names, recursively. | `test_security.py::test_forbidden_keys_rejected_everywhere` |
| Command / argv / env injection via the request | `command`, `commands`, `argv`, `args`, `cmd`, `shell`, `exec`, `executable`, `script`, `env`, `cwd`, `eval`, `expression` are rejected wherever they appear in a request document, at any nesting depth (`model._reject_forbidden`). | `test_security.py::test_forbidden_keys_rejected_everywhere` |
| JavaScript / HTML / CSS execution | There is no such engine anywhere in this pipeline; text fields are plain strings passed as ffmpeg-skill `drawtext` arguments. | `test_security.py::test_no_html_css_js_engine` |
| Path traversal / workspace escape | `PathPolicy.resolve_write_path` rejects `..` segments and resolves the deepest existing ancestor so a symlinked directory cannot point outside the workspace; `PathPolicy.resolve_input` resolves symlinks (`strict=True`) before every check. | `test_security.py::test_path_traversal_rejected`, `test_security.py::test_symlink_escape_rejected` |
| Symlink escape (input side) | `resolve_input` resolves the real path with `Path.resolve(strict=True)` before the `allowed_input_roots` check, so a symlink inside an allowed root pointing outside it is caught. | `test_security.py::test_symlink_escape_rejected` |
| Output overwriting the input, or an unrequested existing file | `output.path == video.path` is `OUTPUT_ERROR`; an existing output without `overwrite: true` is `OUTPUT_ERROR`. | `test_security.py::test_output_cannot_be_input`, `test_integration.py::test_output_exists_without_overwrite` |
| Unsafe / non-portable file names | `security.check_filename` rejects control characters, Windows reserved device names (`CON`, `NUL`, `COM1`..), trailing dot/space, and `-`-prefixed names (which some tools would parse as a flag), on every platform. | `test_security.py::test_check_filename_*` |
| Font file as an attack surface | `font_file` goes through the exact same `PathPolicy.resolve_input` as video/image assets, plus an allowed-extension check (`.ttf`/`.otf`/`.ttc`); it is never treated as executable. | `test_security.py::test_font_file_path_policy` |
| Environment injection into the child process | `adapter._clean_env()` passes only an explicit allowlist of environment variables to `ffmpeg-skill`'s subprocess; the request can never add or override an environment variable (`env`/`cwd` are also forbidden field names). | `test_security.py::test_env_allowlist` |
| Silent fallback to a different font/asset | An unknown `font_id` is `MISSING_INPUT`; a missing `image_path`/`font_file` is `INVALID_INPUT`/`PATH_NOT_ALLOWED`. Never a substituted default reported as success (ADR-7). | `test_unit.py::test_unknown_font_id_rejected` |
| Claiming support for an unimplemented feature | `contract`/`doctor` are generated from the same tables `executor.py` renders from (`model.ELEMENT_TYPES`, `model.ANIMATION_KINDS`); `UNSUPPORTED_ELEMENT_TYPES`/`UNSUPPORTED_ANIMATIONS` are reported separately and never marked `supported`. | `test_contract.py::test_contract_matches_implementation` |

## What is explicitly out of scope for this Skill's own enforcement

- `ffmpeg`/`ffprobe` binary integrity and their own CVEs: this Skill trusts the `ffmpeg-skill` checkout it locates
  (version-window checked, see `docs/ffmpeg-skill.md`) exactly as `audio-production-skill` does.
- Malicious video/image *content* (e.g. a crafted file designed to exploit an ffmpeg decoder bug): out of scope
  for a Skill whose job is to call ffmpeg-skill's own tools, which is also `ffmpeg-skill`'s own stated boundary.
