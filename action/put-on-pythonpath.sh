#!/bin/sh
# Publish the bundled package path through the runner's file commands.
set -eu
source_path=$GITHUB_ACTION_PATH/src
# A newline in a single-line file command could introduce another entry.
case $source_path in
*'
'*)
	echo "gh-transient: bundled source path holds a newline" >&2
	exit 1
	;;
esac
case $EXPORT_PYTHONPATH in
true)
	# Later steps can still import the job's own dependencies.
	value=$source_path${PYTHONPATH:+:$PYTHONPATH}
	case $value in
	*'
'*)
		echo "gh-transient: PYTHONPATH holds a newline; refusing to write it to GITHUB_ENV" >&2
		exit 1
		;;
	esac
	printf 'PYTHONPATH=%s\n' "$value" >>"$GITHUB_ENV"
	;;
false) ;;
*)
	echo "gh-transient: export-pythonpath must be true or false" >&2
	exit 1
	;;
esac
printf 'source-path=%s\n' "$source_path" >>"$GITHUB_OUTPUT"
