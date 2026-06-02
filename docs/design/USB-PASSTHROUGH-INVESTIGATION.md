<!-- SPDX-License-Identifier: MIT -->
# USB passthrough investigation (#286)

Why a YubiKey passes through to the Windows guest but an external USB3
drive does not, and what it would take to fix it.

Branch: `explore/usb-container-hotplug`. Builds on the `security_opt:
label=disable` fix already on `main` (PR #411).

## TL;DR

Live USB hot-plug via **QEMU's own `usb-host`** is not achievable here:
dockur runs QEMU inside a **rootless Podman container**, and QEMU's libusb
device list is **frozen at container/QEMU start** — the container's separate
network namespace (plus no `udevd` / `/run/udev`) means libusb's udev
netlink hotplug monitor never receives kernel uevents. So `device_add
usb-host` can only attach a device present **and at the same bus address**
as at QEMU start. VirtualBox / virt-manager succeed because they are **host
processes** (host netns, live libusb hotplug) — the containerization is the
difference.

**But live IS achievable by bypassing the container:** redirect USB from a
**host** process (`usbredirect`) into a QEMU `usb-redir` socket channel —
the host process has live libusb hotplug, so attach/detach is truly live.
Write access needs no persistent udev rule — run just `usbredirect` under
`pkexec`/`sudo` at attach time (transient root, nothing persistent touched).
See "Live fix that bypasses the container" below. Feasible at every layer
tested; end-to-end (device-in-Windows) not yet smoke-verified.

## The layered walls (each independently necessary)

| # | Wall | Status |
|---|------|--------|
| 1 | **SELinux** — `container_t` can't open `usb_device_t` nodes | **Fixed** (#411, `security_opt: label=disable`, scoped to this container) |
| 2 | **Node write permission** — QEMU usb-host needs `O_RDWR`; udev tags only some classes `uaccess` | Needs a per-device udev rule (sudo, once) |
| 3 | **Frozen libusb list** — QEMU's device list never updates after start | The real blocker for "live"; requires recreate to refresh |

### Wall 2 — write permission

`udev` grants the active-seat user an `uaccess` ACL only to certain device
classes (security keys, input, sound, cameras). USB **mass storage**,
**Bluetooth**, etc. get **no** `uaccess` tag, so their `/dev/bus/usb/BBB/DDD`
node stays `root:root rw-rw-r--`. The rootless container's user (mapped
from container-root via the userns) is neither owner nor group, so it gets
only `other::r--` = **read-only**. QEMU usb-host needs to open the node
`O_RDWR` to claim interfaces, so it can't.

- YubiKey works because udev tags it `TAGS=...uaccess...` → `user:<you>:rw`.
- Fix: a per-device udev rule, installed once with sudo, persistent across
  replug/reboot:
  ```
  SUBSYSTEM=="usb", ATTR{idVendor}=="2109", ATTR{idProduct}=="0715", MODE="0660", TAG+="uaccess"
  ```
  (`setfacl -m u:<you>:rw <node>` works too but is wiped on every replug —
  and USB3 devices re-enumerate their address frequently.)

### Wall 3 — the frozen libusb device list (the deep one)

QEMU's `usb-host` backend uses libusb. libusb on Linux keeps its device
list live via a udev/netlink hotplug monitor thread
(`udev_monitor_new_from_netlink`). Inside the rootless container that
monitor receives **no events** — the container is in a separate network
namespace (kernel uevents are netns-scoped) and there is no `udevd` /
`/run/udev`. So `libusb_get_device_list` returns the **boot-time
snapshot**, forever.

**Evidence (independent of any timing/replug coordination):**

HMP probe against the running guest, `device_add usb-host,hostbus=2,hostaddr=N`:

| Device | Host state | QEMU response |
|--------|-----------|---------------|
| WD `1058:2626` @ addr 20 (stable since QEMU boot) | present | `failed to **open** 2:20` — **found**, perm-only |
| SSD `2109:0715` @ addr 30 (re-enumerated after boot) | present (sysfs + fresh `lsusb` both confirm) | `failed to **find** 2:30` — **not in QEMU's list** |

Corroboration: 6+ long-lived libusb monitors (a `ctypes` poller of
`libusb_get_device_list`) — in the default netns, in `--network=host`, and
in `--network=host` **with `/run/udev` bind-mounted** — every one stayed
**frozen within its lifetime**, while the SSD's address churned
`30→33→34→36→39` *across* runs (proving replugs happened, just never
visible to a single long-lived context). A **fresh** context (each new
`lsusb` / monitor) always sees the current state. So it is specifically
the long-lived (QEMU) context that goes stale.

Notes:
- USB3 is not special per se — `qemu-xhci` has `p3=7` SuperSpeed ports, and
  the (stable) USB3 WD **is** in QEMU's list. The killer is post-boot
  address churn + the frozen list. USB3 just churns more (link re-training).
- `--network=host` + `/run/udev` mount did **not** restore hotplug in
  testing. (It may be theoretically possible with a running udevd in the
  right netns, but it would break WinPodX's `127.0.0.1:port` mapping model
  and was not made to work.)

## Consequence

`device_add usb-host` for a device not in QEMU's frozen list produces an
empty **1.5 Mb/s "USB Host Device"** stub — it never opens the real device.
The YubiKey "works live" only because it was present + stable at QEMU start.

So the current `usb_live` live-attach design (monitor `device_add` at
runtime) cannot work for the general case.

## Proposed fix — boot-time passthrough (recreate-to-attach)

Treat USB like PCI: pass assigned USB devices on QEMU's **start-up** args so
the fresh boot enumeration grabs them.

- `compose._qemu_arguments_for_host` already has the builder
  (`devices.qemu_device_args` emits `-device usb-host,vendorid=0x..,productid=0x..`)
  but only calls it for PCI. Extend it to USB.
- Match by **vendorid/productid** (not hostbus/hostaddr) so an absent device
  yields a pending device rather than aborting boot.
- Target dockur's controller: `bus=xhci.0` — **verify ordering**: our
  `ARGUMENTS` must be appended after dockur emits `-device qemu-xhci,id=xhci`,
  or `bus=xhci.0` won't resolve.
- Requires the node writable at boot → the Wall-2 udev rule.
- UX change: USB attach becomes **recreate-on-attach** (like PCI), not live.
  Update `cli/device.py` + the GUI Devices tab messaging; revisit the
  `usb_live` flag semantics.

### Open questions to settle on real hardware (before merge)

1. Does `-device usb-host,vendorid=,productid=` abort QEMU boot when the
   device is absent, or create a pending device? (Determines whether we can
   always emit it or must gate on presence.)
2. Does `bus=xhci.0` resolve from `ARGUMENTS`, or do we need our own
   controller?
3. Does a boot-added device actually appear in Windows end-to-end (not just
   a stub)?
4. Address-churning devices (some USB3 bridges/docks re-enumerate
   repeatedly) may still miss the boot enumeration window — acceptable?

## Live fix that bypasses the container — usbredir + transient root

The frozen-libusb wall only applies to QEMU **inside** the container. A
**host** process has live libusb hotplug (that's why VirtualBox /
virt-manager work). So redirect USB from the host:

```
[host] usbredirect --device VID:PID  <--socket-->  [qemu] usb-redir chardev  -->  Windows (native driver)
       ^ host process => live libusb hotplug => true live attach/detach
```

- QEMU side: `-chardev socket,id=urN,... -device usb-redir,chardev=urN`.
  Confirmed: dockur's QEMU has the `usb-redir` device; the channel can even
  be added to a running guest via HMP `chardev-add` + `device_add usb-redir`
  (no recreate). USB-over-socket is the same mechanism SPICE uses.
- Host side: `usbredirect` (openSUSE `usbredir` package) grabs the device
  with the **host's** libusb and pipes it to QEMU. Live: spawn = attach,
  kill = detach.
- **Write permission, without a persistent system change** (the user does
  not want to touch udev rules): run **just `usbredirect`** under
  `pkexec`/`sudo` at attach time. Root opens the root-owned device node
  directly — no udev rule, no group, nothing persistent. The privileged
  process is one small short-lived USB forwarder, not WinPodX. (Contrast:
  VirtualBox ships a broad install-time udev rule + `vboxusers` group; we
  can avoid that entirely with transient root.)
- Windows guest sees the **real device with its native driver** (URB-level
  redirection), not a translated/class share.

### Transport snag (the one open item)

The usbredir socket must be reachable host<->container. On this host the
podman bridge firewall blocks **both** directions for arbitrary ports
(host->10.89.0.2:port refused; container->10.89.0.1:port refused). So the
socket has to ride WinPodX's existing **compose `ports:` mapping**
(`127.0.0.1:<port>:<port>`, same as RDP/VNC/agent) — which means a
**one-time recreate** to add the `usb-redir` channel + port-map. After
that, attach/detach is fully live via `usbredirect`. (The HMP live-add of
the channel works but is moot until the socket is reachable.)

### Status

Feasible at every layer tested (QEMU `usb-redir` ✓, HMP channel-add ✓, host
`usbredirect` ✓, `pkexec` ✓, transient-root model ✓). **End-to-end not yet
verified** — device-appears-in-Windows needs the port-mapped socket wired
(one recreate) + a real smoke. No blind compose change before that smoke.

### Proposed WinPodX integration

1. compose: add a `usb-redir` chardev (socket server) + `127.0.0.1:<port>:<port>`
   map per concurrent USB slot (e.g. 4 slots). One-time recreate when the
   feature is enabled.
2. `device attach <usb>`: spawn `pkexec usbredirect --device <vid:pid> --to
   tcp:127.0.0.1:<port>` (transient root, no persistent change). `detach`:
   kill that process. Replaces the broken qemu-libusb `live_attach`.
3. GUI Devices tab drives the same; surface the pkexec prompt / failures.
4. Security keys etc. (uaccess) can still use the existing path without root.

## What works today (shipped on main)

- `uaccess` USB2 devices (security keys) attach **live**.
- The `security_opt: label=disable` SELinux lift (#411).
- For files on an external drive, FreeRDP drive redirection
  (`\\tsclient\media`, already wired) is the right tool and works for any
  filesystem — raw passthrough of a LUKS/ext4 disk is useless to Windows
  anyway.
