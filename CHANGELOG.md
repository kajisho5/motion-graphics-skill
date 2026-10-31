# Changelog

## v0.2.6 — 2026-10-31

- Single-source the version: importlib.metadata fallback and a release-bump self-check (roadmap step 1) (#32) (db2a066)

## v0.2.5 — 2026-10-30

- ci: pin third-party actions by commit SHA (#31) (d634588)

## v0.2.4 — 2026-10-30

- fix: convert template colour names to RRGGBB, refuse @alpha on them, say so in the contract (ADR-20) (#30) (d2c2fc5)

## v0.2.3 — 2026-10-30

- fix: distinct provides ids, contract_version and ffmpeg-skill dependency in the contract (#29) (4438320)

## v0.2.2 — 2026-10-30

- fix: report the release version, single-sourced from pyproject.toml (#28) (891f0b8)

## v0.2.1 — 2026-10-30

- Add ECOSYSTEM_ROADMAP.md: gaps, wiring and PR-sized steps from the 2026-10-30 ecosystem audit (#27) (94983f4)

## v0.2.0 — 2026-10-29

- Implement sticker, hook and meme elements and options.platform; correct the shape and template claims (#26) (b927791)

## v0.1.2 — 2026-10-25

- Add the Sponsor badge and .github/FUNDING.yml (#25) (18feee5)

## v0.1.1 — 2026-10-23

- ci: bump GitHub Actions, and resolve release versions with release-drafter v7 dry-run (#24) (c6b42da)

## v0.1.0 — 2026-10-23

- Add release automation, PR autolabeling, Dependabot, CodeQL, PR template, SECURITY.md (#14) (f4f02c6)
- ci: keep CI green on Ubuntu 26.04 (Python 3.9, ffmpeg 8.0) (#20) (415d1d9)
- docs(readme): fresnel-style header with a Liquid Glass app icon (#17) (0ec7d2d)
- fix: work with any ffmpeg-skill version (capability detection, no version window) (#18) (27b8815)
- Reconcile FLAGS_USED["graphics"/"overlay"] with what executor.py actually emits (#13) (94ba430)
- Add video_overlay (PiP/chroma-key), audio_stream, and dropped_non_av_streams surfacing (#11) (32e762e)
- Refresh CLAUDE.md after closing the bug/chapter/progress/countdown arc; add a security regression test (#9) (a9e8d38)
- Implement countdown element type (#8) (01614c7)
- Implement progress (bottom fill bar) element type (#7) (1aeb95a)
- Implement chapter (corner chip) element type (#6) (1e3229c)
- Refresh CLAUDE.md after merging PR #2/#3/#4 (#5) (76d6d52)
- Implement bug (persistent corner text watermark) element type (#4) (5d7faed)
- Add CLAUDE.md: durable repository-local state for future maintainer sessions (#3) (556118f)
- Add provides: publish Capability ids for cross-repository discovery (#2) (f41ca9e)
- Implement motion-graphics-skill: deterministic motion-graphics rendering execution (#1) (b86a224)
- Initial commit (8e53b4d)
