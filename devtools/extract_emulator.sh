#!/bin/bash
# Tars chosen stores of a rooted EMULATOR to the workstation (adb root must already be on). Used to take
# a before and an after copy around a walk, so what the walk changed can be measured.
# A tar of all of /data was tried first and was cut short three times out of four, each time inside a
# file another app was still downloading. So this takes named folders only, and the archive counts as
# good only when the workstation can list it to the end.
# Usage: extract_emulator.sh <serial> <out.tar>
serial="$1"
out="$2"
[ -n "$serial" ] && [ -n "$out" ] || { echo "usage: $0 <serial> <out.tar>"; exit 2; }
case "$serial" in emulator-*) ;; *) echo "emulators only"; exit 2;; esac
[ -e "$out" ] && { echo "$out exists"; exit 2; }
adb -s "$serial" shell id </dev/null | grep -q 'uid=0' || { echo "adb is not root on $serial"; exit 1; }
PATHS="/data/data/com.google.android.apps.messaging/databases /data/data/com.google.android.apps.messaging/shared_prefs /data/data/com.android.providers.telephony /data/user_de/0/com.android.providers.telephony /data/system/usagestats /data/system_ce/0 /data/system/users /data/system/packages.xml /data/misc/adb"
# The phone's stderr is sent to /dev/null there: exec-out puts it in the same stream as the archive and
# one warning line makes the archive unreadable.
adb -s "$serial" exec-out "tar -cf - $PATHS 2>/dev/null" </dev/null > "$out" 2> "$out.stderr"
if ! tar -tf "$out" > "$out.list" 2>> "$out.stderr"; then
  echo "FAILED: $out does not list to the end"; tail -3 "$out.stderr"; exit 1
fi
echo "$out: $(wc -l < "$out.list" | tr -d ' ') members, $(du -h "$out" | cut -f1), sha256 $(shasum -a 256 "$out" | cut -d' ' -f1)"
grep -v "removing leading" "$out.stderr" | head -5
