`silent-take.mp3` is generated silence for the native read-aloud browser test:
120 seconds, mono, 22,050 Hz. It contains no recorded voice or vendor output.
Its generator is this command, run with FFmpeg 5.1.9 and libmp3lame:

```sh
ffmpeg -f lavfi -i anullsrc=r=22050:cl=mono -t 120 -c:a libmp3lame -q:a 9 test/fixtures/silent-take.mp3
```
