#!/bin/sh
set -e

usage() {
cat << EOF
Usage: ${0##*/} [-c] [-d]

Use poetry to regenerate locked versions of Galaxy dependencies.
Use -c to automatically commit these changes (be sure you have no staged git
changes).

EOF
}

commit=0
while getopts ":hcd" opt; do
    case "$opt" in
        c)
            commit=1
            ;;
        h)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$VIRTUAL_ENV" ]; then
    echo "Please run this script inside a virtual environment!"
    exit 1
fi

THIS_DIRECTORY="$(cd "$(dirname "$0")" > /dev/null && pwd)"

pip install --upgrade pip setuptools poetry
poetry update -vv
poetry export -f requirements.txt --without-hashes --output "$THIS_DIRECTORY/pinned-requirements.txt"
poetry export --dev -f requirements.txt --without-hashes --output "$THIS_DIRECTORY/dev-requirements.txt"

if [ "$commit" -eq 1 ]; then
        git add -u "$THIS_DIRECTORY"
        git commit -m "Rev and re-lock Galaxy dependencies"
fi
