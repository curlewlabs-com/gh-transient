#!/bin/sh
# The action's one step: prepend the package beside this action to PYTHONPATH
# for every later step of the job, keeping whatever PYTHONPATH the job already
# set. GITHUB_ENV is read a line at a time, so a newline in that value would
# set a second variable of its own; it is refused instead.
set -eu
value=$GITHUB_ACTION_PATH/src${PYTHONPATH:+:$PYTHONPATH}
case $value in
*'
'*)
	echo "gh-transient: PYTHONPATH holds a newline; refusing to write it to GITHUB_ENV" >&2
	exit 1
	;;
esac
printf 'PYTHONPATH=%s\n' "$value" >>"$GITHUB_ENV"
