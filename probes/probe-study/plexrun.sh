#!/bin/bash
# plexrun.sh CODE... : for each lab variant, open it in Plex for Android, play 15 s, seek, record.
S=$(cd "$(dirname "$0")" && pwd)  # outputs go under $S/${RUNS:-runs}
a(){ adb -s ${ADB_SERIAL:-127.0.0.1:15555} "$@"; }
ui(){ python3 $S/ui.py "$@"; }
for C in "$@"; do
  O=$S/${RUNS:-runs}/plex/$C; mkdir -p $O
  a shell am force-stop com.plexapp.android
  a shell monkey -p com.plexapp.android -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 7
  a shell input tap 618 213; sleep 2
  a shell input text "$C"; sleep 7
  ui tap "^ \| $C .*Movie$" > $O/steps.txt || { echo "$C: not found"; continue; }
  sleep 4
  ui dump > $O/details.txt; a exec-out screencap -p > $O/details.png
  T0=$(ssh ${REXLAB_SSH:-zen} date +%H:%M:%S)
  ui tap "^(Watch|Resume[^|]*) \|" >> $O/steps.txt; sleep 15
  a shell dumpsys activity activities | grep -m1 topResumedActivity > $O/top.txt
  a exec-out screencap -p > $O/play15.png; ui dump > $O/ui15.txt
  a shell 'input tap 1200 300; sleep 0.8; input swipe 376 964 1440 964 800'; sleep 1
  ui dump > $O/ui_seek.txt; sleep 9
  a exec-out screencap -p > $O/seek.png; ui dump > $O/ui_after.txt
  a shell input keyevent BACK; sleep 1; a shell input keyevent BACK; sleep 2
  ssh ${REXLAB_SSH:-zen} "journalctl --user -u rexlab --since $T0 --no-pager" | grep -E "INFO|WARN|ERROR|TIMELINE|transcode" | grep -vE '"GET /(photo|library/metadata/[0-9]+/thumb|service/ultrablur)' | cut -c38- > $O/rex.log
  ssh ${REXLAB_SSH:-zen} "journalctl --user -u rexlab-cdn --since $T0 --no-pager -o cat" | grep "^CDN" > $O/cdn.log
  echo "$C cdn: $(grep -c "192.168.88.67" $O/cdn.log) phone reads, $(grep -c "192.168.88.244\|127.0.0.1" $O/cdn.log) rex reads"
  echo "$C: $(grep -cE 'PART ' $O/rex.log) part reads, $(grep -E 'DECISION' $O/rex.log | head -1 | cut -c1-80)"
done
