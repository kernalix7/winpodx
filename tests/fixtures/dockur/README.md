# Dockur progress fixture

The two `msg-v6.05-32cc9271-*.html` files reproduce the exact `/msg.html` bytes
written by Dockur v6.05's `qemux/qemu:7.48` base scripts. `progress.sh` wraps a
message ending in `...` with `<p class="loading">`; `finish.sh` sends a bare
non-loading message through the same HTML escape/write path. `record.json`
binds both samples and their source URLs to the shipped `DOCKUR_IMAGE_PIN`.
Tests never start a container or contact localhost.

When the image pin changes, verify the new Dockur Dockerfile's base image and
refresh these source-derived samples. A manual runtime capture, when available,
must use an isolated disposable container on a random loopback port with no user
storage; it must never run in pytest or CI.
