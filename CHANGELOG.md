# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries for releases before this file existed were generated from commit subjects.

## [2.0.1] - 2026-09-03

- Fix GEM-TEMP always writing null (#872)
- Require stable pyobs-core>=2.0.0
- Remove stale poetry.lock; project now uses uv.lock
- Drop <3.14 upper bound on requires-python
- Add .readthedocs.yml
- Add Sphinx docs and rewrite README
- Gate auto-merge on the PR author, not the event actor
- Enable Dependabot auto-merge for patch/minor updates
- Add regression test for mixin kwargs reaching Fits/MotionStatus mixins
- Convert GeminiFocuserRotator to cooperative super() init chain
- Add baseline test suite and CI (pytest), grouped Dependabot
- Upgrade uv.lock to clear open Dependabot alerts
- Require pyobs-core>=2.0.0.dev48
- Update uv-build requirement from <0.10.0,>=0.9.14 to >=0.9.14,<0.12.0
- Migrate to pyobs-core 2.0, uv, and the fleet's standard CI baseline
- Add dependabot.yml, targeting develop for PRs
- python 3.11

## [2.0.0] - 2026-08-26

- Require stable pyobs-core>=2.0.0
- Remove stale poetry.lock; project now uses uv.lock
- Drop <3.14 upper bound on requires-python
- Add .readthedocs.yml
- Add Sphinx docs and rewrite README
- Gate auto-merge on the PR author, not the event actor
- Enable Dependabot auto-merge for patch/minor updates
- Add regression test for mixin kwargs reaching Fits/MotionStatus mixins
- Convert GeminiFocuserRotator to cooperative super() init chain
- Add baseline test suite and CI (pytest), grouped Dependabot
- Upgrade uv.lock to clear open Dependabot alerts
- Require pyobs-core>=2.0.0.dev48
- Update uv-build requirement from <0.10.0,>=0.9.14 to >=0.9.14,<0.12.0
- Migrate to pyobs-core 2.0, uv, and the fleet's standard CI baseline
- Add dependabot.yml, targeting develop for PRs

## [1.0.1] - 2023-12-03

- python 3.11

## [1.0.0] - 2022-09-13

- Maintenance release (dependency and metadata updates only).

## [0.2.2] - 2022-09-04

- fixed script

## [0.2.1] - 2022-09-04

- changed poetry install script

## [0.2.0] - 2022-09-04

- added pypi script
- added README and LICENSE
- first commit
