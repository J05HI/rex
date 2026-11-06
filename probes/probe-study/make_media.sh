#!/bin/bash
# make_media.sh <lab dir>: the three test files of docs/ffprobe-study.md, as they were made on 2026-11-05/06.
#   media/a.mp4  120 s, H.264 High 1080p30 + AAC 2.0 440 Hz, MP4 (faststart)
#   media/b.mkv  120 s, HEVC Main 10 1080p30 + E-AC-3 5.1 660 Hz, Matroska
#   media/m.mkv  a.mp4's video + English AAC 2.0 440 Hz (default), Japanese AC-3 5.1 880 Hz, English
#                Commentary AAC 2.0 220 Hz, English SRT, styled ASS, forced English PGS (mksubs.py)
# FFMPEG names an ffmpeg with libx264, libx265 and the ac3/eac3 encoders (jellyfin-ffmpeg 8.1 was used).
set -e
LAB=${1:?lab dir}; FF=${FFMPEG:-ffmpeg}; HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$LAB/media" "$LAB/subs"
"$FF" -hide_banner -loglevel error -y -f lavfi -i testsrc=size=1920x1080:rate=30:duration=120 \
  -f lavfi -i sine=frequency=440:duration=120:sample_rate=48000 -ac 2 \
  -c:v libx264 -preset veryfast -profile:v high -pix_fmt yuv420p -b:v 4M -c:a aac -b:a 128k -movflags +faststart "$LAB/media/a.mp4"
"$FF" -hide_banner -loglevel error -y -f lavfi -i testsrc=size=1920x1080:rate=30:duration=120 \
  -f lavfi -i sine=frequency=660:duration=120:sample_rate=48000 \
  -filter_complex "[1:a]pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0[a]" -map 0:v -map "[a]" \
  -c:v libx265 -preset ultrafast -pix_fmt yuv420p10le -x265-params log-level=error -b:v 4M -c:a eac3 -b:a 384k "$LAB/media/b.mkv"
python3 "$HERE/mksubs.py" "$LAB/subs"
"$FF" -hide_banner -loglevel error -y -i "$LAB/media/a.mp4" \
  -f lavfi -i sine=frequency=440:duration=120:sample_rate=48000 \
  -f lavfi -i sine=frequency=880:duration=120:sample_rate=48000 \
  -f lavfi -i sine=frequency=220:duration=120:sample_rate=48000 \
  -i "$LAB/subs/eng.srt" -i "$LAB/subs/styled.ass" -f sup -i "$LAB/subs/forced.sup" \
  -filter_complex "[2:a]pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0[jp]" \
  -map 0:v -map 1:a -map "[jp]" -map 3:a -map 4 -map 5 -map 6 \
  -c:v copy -c:a:0 aac -ac:a:0 2 -b:a:0 128k -c:a:1 ac3 -b:a:1 384k -c:a:2 aac -ac:a:2 2 -b:a:2 96k \
  -c:s:0 srt -c:s:1 ass -c:s:2 copy \
  -metadata:s:a:0 language=eng -metadata:s:a:0 title=English \
  -metadata:s:a:1 language=jpn -metadata:s:a:1 title=Japanese \
  -metadata:s:a:2 language=eng -metadata:s:a:2 title=Commentary \
  -metadata:s:s:0 language=eng -metadata:s:s:0 "title=English SRT" \
  -metadata:s:s:1 language=eng -metadata:s:s:1 "title=English ASS styled" \
  -metadata:s:s:2 language=eng -metadata:s:s:2 "title=English PGS forced" \
  -disposition:a:0 default -disposition:a:1 0 -disposition:a:2 comment \
  -disposition:s:0 0 -disposition:s:1 0 -disposition:s:2 forced "$LAB/media/m.mkv"
ls -la "$LAB/media"
