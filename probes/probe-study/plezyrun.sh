#!/bin/bash
# plezyrun.sh CODE... : for each lab variant, open it in Plezy, play 15 s, seek to 60%, record.
S=$(cd "$(dirname "$0")" && pwd)  # outputs go under $S/${RUNS:-runs}
a(){ adb -s ${ADB_SERIAL:-127.0.0.1:15555} "$@"; }
ui(){ python3 $S/ui.py "$@"; }
for C in "$@"; do
  O=$S/${RUNS:-runs}/plezy/$C; mkdir -p $O
  a shell am force-stop com.edde746.plezy
  a shell monkey -p com.edde746.plezy -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 7
  ui tap "\| Later$" >/dev/null 2>&1 && sleep 1
  ui tap "Search\sTab" >/dev/null; sleep 2
  a shell input text "$C"; sleep 5
  ui tap "^ \| $C .*movie" > $O/steps.txt || { echo "$C: not found"; continue; }
  sleep 4
  ui dump > $O/details.txt; a exec-out screencap -p > $O/details.png
  T0=$(ssh ${REXLAB_SSH:-zen} date +%H:%M:%S)
  ui tap "\| (Play|Resume)$" >> $O/steps.txt; sleep 15
  a shell dumpsys activity activities | grep -m1 topResumedActivity > $O/top.txt
  a exec-out screencap -p > $O/play15.png
  a shell input tap 1200 760; sleep 0.8; ui dump > $O/ui15.txt
  a shell input tap 1483 927; sleep 9
  a exec-out screencap -p > $O/seek.png
  a shell input tap 1200 760; sleep 0.8; ui dump > $O/ui_after.txt
  a shell input keyevent BACK; sleep 1; a shell input keyevent BACK; sleep 2
  ssh ${REXLAB_SSH:-zen} "journalctl --user -u rexlab --since $T0 --no-pager" | grep -E "INFO|WARN|ERROR|TIMELINE|transcode" | grep -vE '"GET /(photo|library/metadata/[0-9]+/thumb|service/ultrablur)' | cut -c38- > $O/rex.log
  ssh ${REXLAB_SSH:-zen} "journalctl --user -u rexlab-cdn --since $T0 --no-pager -o cat" | grep "^CDN" > $O/cdn.log
  echo "$C cdn: $(grep -c "192.168.88.67" $O/cdn.log) phone reads, $(grep -c "192.168.88.244\|127.0.0.1" $O/cdn.log) rex reads"
  echo "$C: $(grep -cE 'PART ' $O/rex.log) part reads; $(grep -oE 'DECISION .{0,40}' $O/rex.log | head -1); ui: $(grep -oE '[0-9]+:[0-9]{2} -[0-9:]+' $O/ui15.txt $O/ui_after.txt | tr '\n' ' ')"
done
