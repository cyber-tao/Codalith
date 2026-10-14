#!/bin/sh
set -eu

codalith_root=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
codalith_installer="$codalith_root/scripts/install_agents.py"

if command -v uv >/dev/null 2>&1; then
    exec uv run --no-project --python '>=3.11' -- python -X utf8 "$codalith_installer" "$@"
fi

for codalith_python in python3 python; do
    if command -v "$codalith_python" >/dev/null 2>&1 &&
        "$codalith_python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        exec "$codalith_python" -X utf8 "$codalith_installer" "$@"
    fi
done

printf '%s\n' 'Codalith setup requires uv or Python 3.11+. Install uv: https://docs.astral.sh/uv/getting-started/installation/' >&2
exit 2
