#!/usr/bin/env sh
# Compatibility wrapper for the cross-platform Python UE acceptance runner.
set -eu
exec uv run python -m jobs.ue_eval "$@"
