# Radioberry Juice on Linux: missing UDP packets and a maxed-out waterfall

This repo is a write-up and fix for a **Radioberry 2.x CL025** on a **Radioberry Juice board** (FT2232H USB) running on an x86_64 Debian 13 PC with **piHPSDR**.

## Symptoms

- piHPSDR detects the radio (HermesLite V2, protocol 1), but the waterfall and panadapter sit at maximum, around **+200 dB**.
- The piHPSDR log is full of sequence errors, always exactly one frame missing:

  ```
  06:03:23.411 SEQ ERROR: last 76977, recvd 76979
  06:03:23.490 SEQ ERROR: last 77007, recvd 77009
  06:03:23.569 SEQ ERROR: last 77037, recvd 77039
  ```

## Results

Measured with [`tools/p1test.py`](tools/p1test.py) directly against the gateway on UDP port 1024:

| | Frames | Lost | IQ data |
|---|---|---|---|
| Before (libusb-shim gateway), 48 kHz, 10 s | 3702 | **102 (2.68 %)** | about half the samples garbage, peaks at 0 dBFS |
| After (this fix), 48 kHz, 20 s | 7623 | **0** | clean; noise floor about −146 dBFS/bin when tuned to 7.074 MHz |
| After (this fix), 192 kHz, 20 s | 30472 | **0** | clean |

---

## How the setup works

```
piHPSDR ──UDP 1024 (openHPSDR protocol-1)──> radioberry-juice (gateway on the PC)
                                                  │ USB 2.0 high speed
                                            FT2232H on the Juice board
                                    channel A: 245 synchronous FIFO  → IQ stream
                                    channel B: bit-bang (DCLK/DATA0/nCONFIG) → FPGA passive-serial config
                                                  │
                                         Radioberry FPGA (10CL025) + AD9866
```

The FPGA has no config flash of its own. The gateway uploads `radioberry.rbf` through channel B every time it starts, then streams 1032-byte protocol-1 frames through channel A. The FPGA writes the frame sequence numbers itself. So a sequence gap means data was lost **between the FT2232H and the gateway**, not on the network.

---

## Root causes (four separate bugs)

### 1. The libusb "ftd2xx shim" corrupted the IQ stream

The previous Linux build replaced FTDI's D2XX library with a homemade libusb shim (`ftd2xx_shim.c` + `ftdi_rx.c`). Every 512-byte FT2232H USB packet starts with 2 modem-status bytes that must be removed. The shim removed 2 bytes at the wrong offsets. A raw frame from the gateway shows it:

```
  72: 04 dc 03 00 00 00 00 00 | 14 00 00 00 00 00 | 04 e4 2f ...   <- a sample is 2 bytes short
 504: 7a 00 00 00 32 60 00 00 ...                                  <- "32 60" = FTDI status bytes inside IQ data
1008: ... 04 df e3 00 32 60 00 00 00 00 04 c9 91 ...               <- again, ~510 bytes later
```

(A protocol-1 sample is 8 bytes: I 24-bit, Q 24-bit, mic 16-bit.)

This produced both symptoms:

- **Maxed-out waterfall:** after every slip, the rest of the sub-frame is misaligned. Bytes like `c2 00 00` get read as samples near full scale, and about 48 % of samples had their low 12 bits zero. WDSP turns that into a spectrum at the top of the scale.
- **Missing packets:** frames hit by a slip fail the `7F 7F 7F` sync check in `stream.c` and get dropped. Exactly one frame at a time is lost, which is the `last N, recvd N+2` pattern.

**Fix:** use the official build in `juice/firmware-extended`, which pa3gsb ships with the FTDI D2XX 1.4.35 library for x86_64, aarch64, armhf and x86_32.

### 2. Channel B is disabled while channel A is in synchronous FIFO mode

The FT2232H datasheet says: when channel A is in FT245 **synchronous** FIFO mode, channel B is not available. The gateway leaves channel A in that mode when it exits, and the chip keeps it until it is reset or power-cycled.

So on every **restart** of the gateway (service restart, crash, piHPSDR re-launch scripts), all the bit-bang writes on channel B were silently ignored. nCONFIG never pulsed and the FPGA was never reprogrammed.

The evidence, from [`tools/ftdi_syncbb_echo.c`](tools/ftdi_syncbb_echo.c) (sync bit-bang returns one pin sample per byte written):

```
iface B sync bitbang=0
wrote 64, read back 0 samples          <- channel B dead while A was in sync FIFO mode

iface A sync bitbang=0                 <- (this also resets A out of sync FIFO)
wrote 64, read back 64 samples

iface B sync bitbang=0
wrote 64, read back 64 samples:        <- channel B works again
ee e0 e0 e0 e0 e0 e0 e0 e0 e7 e7 ef ef ef ef ef
```

With channel A reset, a manual passive-serial load ([`tools/ftdi_ps_load.c`](tools/ftdi_ps_load.c)) behaves exactly as the Cyclone datasheet says:

```
  before                       pins=0xef nSTATUS=1 CONF_DONE=0
  nCONFIG low                  pins=0xe0 nSTATUS=0 CONF_DONE=0
  nCONFIG high                 pins=0xec nSTATUS=1 CONF_DONE=0
  after upload + clocks        pins=0xfc nSTATUS=1 CONF_DONE=1
RESULT gateware/CL025/radioberry.rbf: CONFIGURED (CONF_DONE=1)
```

**Fix:** before opening channel B, open channel A and call `FT_SetBitMode(A, 0, 0)`.

### 3. The official loader cannot read the config pins on Linux

In `gateware.c`, upstream waits for nSTATUS and CONF_DONE by calling `FT_Read()` on channel B in async bit-bang mode. With D2XX 1.4.35 on Linux, that read returns **0 bytes** (the RX queue stays empty). The loop then tests an uninitialised byte:

- `init_gateware_upload()` passes or fails at random.
- `activate_gateware()` can spin forever printing `NSTATUS and NCONF_DONE must be low...`.

An earlier local workaround added timeouts that "continue". That turned a failed FPGA load into a gateway that streams garbage without saying anything.

**Fix:** read the live pin levels with `FT_GetBitMode()`. The patch also:

- holds nCONFIG low for 10 ms (the old code used a 2-byte pulse, a few microseconds);
- checks that nSTATUS actually drops;
- waits for nSTATUS to rise;
- after the upload, **requires CONF_DONE = 1** and otherwise stops with a clear message: `FPGA rejected the gateware ... Check that fpga= in radioberry.props matches the board`.

### 4. Unplug and replug: no waterfall until everything is restarted by hand

When the Juice board is unplugged, or drops off USB because of a power dip, it comes back as a **new** USB device. Four separate things went wrong:

| Problem | Effect |
|---|---|
| **a.** The gateway keeps its handle to the vanished device, and every `FT_Read`/`FT_Write` fails at once. | It never exits, spins at 100 % CPU and floods its log with `us stream time out`. On the test PC that was 17 million lines, 327 MB, within minutes. |
| **b.** The gateway only touches USB while an SDR program is streaming. | An unplug while piHPSDR is closed goes completely unnoticed; the gateway sits on a dead handle. |
| **c.** The old start script exits when no board is present, and the udev "start on plug-in" rule did not work reliably. | After an unplug of more than a few seconds nothing starts the gateway again. |
| **d.** piHPSDR sends `Start` only once, when the radio is started. It goes silent when data stops. | Even a restarted gateway streams to nobody. The waterfall stays empty until the radio is restarted in piHPSDR. |

**Fixes:**

- **a. Exit on stream errors** ([`0002`](patches/0002-juice-stream-exit-on-usb-device-loss.patch), `stream.c`): the gateway exits with code 2 as soon as D2XX reports the device is gone (`FT_DEVICE_NOT_FOUND`), or after 20 stream errors in a row.
- **b. Watch for an unplug even when idle** (same patch): a thread watches the board's sysfs entry (`/sys/bus/usb/devices/*`, `0403:6010`, product `radioberry-juice`) twice a second. If it disappears or changes device number, the gateway exits with code 2.
- **c. Wait for the board** ([`scripts/start-radioberry-juice.sh`](scripts/start-radioberry-juice.sh)): the start script now waits until the board is plugged in, instead of exiting. With `Restart=on-failure`, systemd restarts the script after an exit, it waits for the board, and the loader reloads and verifies the FPGA. No udev rule is needed.
- **d. Resume the stream** ([`0003`](patches/0003-juice-resume-stream-after-gateway-restart.patch), `radioberry.c`):
  - On a UDP `Start`, the gateway saves the client's address in `last-client`, in the gateway directory. The file is refreshed every 2 s while streaming and removed on `Stop`.
  - On startup, if that file is less than 5 minutes old, the gateway sends Start to the FPGA and resumes streaming to that client. piHPSDR's socket is still open, so its waterfall simply continues. piHPSDR's normal command traffic then restores frequency, gain and sample rate.
  - If the client doesn't answer within 10 s (piHPSDR was closed), the gateway stops the stream again and removes the file.
  - The resume path talks to the FPGA with `write_stream()`, not `write_rb_stream()`. The latter takes the external-amplifier semaphore, which is only initialised once the amplifier thread has run, and waiting on it at startup deadlocked the gateway.

**Tests on the PC:**

- **Stream errors:** a USB reset (`USBDEVFS_RESET`) in the middle of a stream.

  ```
  us stream time out (status 2, 1 in a row)
  Radioberry Juice USB stream lost; exiting so the gateway can be restarted.
  ...
  FPGA gateware activated.
  frames=3051 (381/s) gap_events=0 lost=0 loss=0.00% bad=0 seq_restarts=0
  ```

- **Resume:** gateway killed (`kill -9`) while `p1test.py` streamed, 3 rounds. The client never sent a new Start and kept receiving about 10 s later: 11,196–11,312 frames per 40 s run, no gaps apart from the restart.
- **Client gone during the restart:** the gateway logged `No answer from the SDR program after resume; stopping the stream.` and removed the state file. A fresh client afterwards worked normally.
- **Real cable pulls with piHPSDR:**

  ```
  # unplugged while piHPSDR was closed
  Radioberry Juice USB device removed; exiting so the gateway can be restarted.
  Waiting for the Radioberry Juice board (FT2232H 0403:6010) to be plugged in...
  FPGA gateware activated.

  # unplugged while piHPSDR was streaming — waterfall came back without touching piHPSDR
  Radioberry Juice USB stream lost; exiting so the gateway can be restarted.
  Waiting for the Radioberry Juice board (FT2232H 0403:6010) to be plugged in...
  FPGA gateware activated.
  Resuming IQ stream to 192.168.0.95:38756 after gateway restart.
  ```

**If the board keeps dropping off USB,** that is a power or cable problem, not software. The kernel log (`sudo journalctl -k`) then shows `usb_submit_urb returned -121` followed by `USB disconnect`, or `device descriptor read/64, error -32` on replug. This was seen on a battery-powered Raspberry Pi CM5 (uConsole) that shared its internal USB hub with other devices. Power the Juice board and Radioberry separately, or through a powered USB hub.

---

## The patches

All three apply to [pa3gsb/Radioberry-2.x](https://github.com/pa3gsb/Radioberry-2.x) at commit `a9c5139` ("gateware selection added....", 2026-09-13). They were verified by building from a clean checkout:

- [`patches/0001-juice-gateware-loader-linux-d2xx.patch`](patches/0001-juice-gateware-loader-linux-d2xx.patch) changes `juice/firmware-extended/gateware.c` and covers root causes 2 and 3.
- [`patches/0002-juice-stream-exit-on-usb-device-loss.patch`](patches/0002-juice-stream-exit-on-usb-device-loss.patch) changes `juice/firmware-extended/stream.c` and covers root causes 4a and 4b.
- [`patches/0003-juice-resume-stream-after-gateway-restart.patch`](patches/0003-juice-resume-stream-after-gateway-restart.patch) changes `juice/firmware-extended/radioberry.c` and covers root cause 4d.

Root cause 1 is fixed simply by using the official D2XX build instead of the shim.

## Install

Requirements: `build-essential`, `git`. For the test tools only: `python3-numpy`, `libftdi1-dev`.

```bash
git clone https://github.com/Anto5556/radioberry-juice2-.git
cd radioberry-juice2-
bash scripts/install.sh CL025        # or CL016
```

The installer does the following:

1. Clones pa3gsb/Radioberry-2.x into `~/radioberry-juice/Radioberry-2.x` and checks out the tested commit.
2. Applies all patches in `patches/` and builds `juice/firmware-extended` with `linux-Makefile`.
3. Installs the gateway, bundled `libftd2xx.so`, both gateware images and `radioberry.props` (`fpga=CL025`) into `~/radioberry-juice/gateway`.
4. Installs and starts the user service [`systemd/radioberry-juice.service`](systemd/radioberry-juice.service). Any existing unit is backed up first.

### USB access (one time, needs root)

The gateway talks to the FT2232H directly. It needs permission on the USB device, and the kernel `ftdi_sio` driver must not hold the interfaces:

```bash
sudo cp udev/99-radioberry-juice.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"     # log out/in afterwards
```

Then unplug and replug the Juice board. Check it:

```bash
lsusb -d 0403:6010              # "radioberry-juice"
ls /dev/ttyUSB*                 # should be empty
```

### Check it works

```bash
tail -f ~/radioberry-juice/gateway/gateway.log
```

Expect:

```
FPGA CL025: loading gateware/CL025/radioberry.rbf
FPGA ready to receive gateware.
FPGA gateware activated.
Init device succeeded for iq streaming using FT245 protocol.
```

Then run a stream test (with piHPSDR closed):

```bash
python3 tools/p1test.py 127.0.0.1 20 0        # host, seconds, speed (0=48k 1=96k 2=192k 3=384k)
# frames=7623 (381/s) gap_events=0 lost=0 loss=0.00% bad=0
```

The IQ level `p1test.py` prints is not meaningful: it does not tune the NCO, so it only sees the ADC DC offset. For a real receive check, tune and look at the noise floor:

```bash
python3 tools/p1_iqcapture.py --freq 7074000 /tmp/iq.npy
# lost frames: 0
# Q nonzero fraction: 0.993
# samples with low 12 bits zero (garbage indicator): 0.000
# noise floor (median) -146.2 dBFS/bin ...
```

### piHPSDR

- Start piHPSDR. The radio is discovered as **HermesLite V2** on `127.0.0.1` / your LAN IP, MAC `00:01:02:03:04:05`.
- If the waterfall looked wrong before, check **Radio → RX gain calibration**. On the test machine it had been set to `-40`, apparently to hide the garbage. The piHPSDR default for HL2-type radios is `14`. The value is `rx_gain_calibration` in `00-01-02-03-04-05.props`.
- **Unplug and replug is handled automatically.** If piHPSDR is running, its waterfall comes back by itself about 10–15 s after the board is plugged back in. If piHPSDR is closed, wait about 15 s after plugging in before starting it.
- The service is a systemd **user** service, so it runs while you are logged in. To have it run at boot without logging in: `sudo loginctl enable-linger $USER`.

---

## Tools

| File | Purpose |
|---|---|
| `tools/p1test.py` | protocol-1 client: discovery, start, count sequence gaps / bad frames / FPGA counter restarts, rough IQ level |
| `tools/p1_iqcapture.py` | tuned IQ capture to `.npy` with loss, garbage and noise-floor statistics |
| `tools/p1_rawdump.py` | hex dump of a raw 1032-byte frame (how the `32 60` status bytes were found) |
| `tools/ftdi_syncbb_echo.c` | libftdi: sync bit-bang echo on channel A or B (shows channel B disabled) |
| `tools/ftdi_ps_load.c` | libftdi: standalone passive-serial FPGA load that checks nSTATUS/CONF_DONE at each step |
| `tools/ftdi_eeprom_pins.c` | libftdi: read EEPROM channel types and bit-bang pin states |
| `tools/d2xx_pins.c` | same checks through FTDI D2XX (shows `FT_Read` returning 0 bytes in bit-bang mode) |

Build the C tools (stop the gateway first, since it holds the device):

```bash
systemctl --user stop radioberry-juice.service
gcc -O2 -I/usr/include/libftdi1 tools/ftdi_syncbb_echo.c -lftdi1 -o ftdi_syncbb_echo
gcc -O2 -I/usr/include/libftdi1 tools/ftdi_ps_load.c    -lftdi1 -o ftdi_ps_load
./ftdi_syncbb_echo A && ./ftdi_syncbb_echo B
./ftdi_ps_load ~/radioberry-juice/gateway/gateware/CL025/radioberry.rbf
```

`ftdi_ps_load` leaves the FPGA configured, so the gateway reloads it anyway on its next start.

## Other notes from the investigation

- **EEPROM:** both FT2232H channels are set to "245 FIFO". That matches the official `ft2232h-radiojuice-template.xml` and is correct.
- **Board type:** CL025 confirmed. The `CL025` image loads (CONF_DONE=1). Make sure `fpga=` matches your board; with the patch, a wrong image now fails loudly.
- **Harmless warnings:** `SO_PRIORITY: Operation not permitted` and `AMP Connection timed out` (no external amplifier) can be ignored.
- **Old setup:** the libusb-shim gateway from a previous attempt should not be used. Its sources re-synchronise frames in `stream.c`, which only hides the corruption described above.

## Credits

- Radioberry, the Juice board, the gateway and the gateware: Johan Maas, PA3GSB — https://github.com/pa3gsb/Radioberry-2.x
- piHPSDR: DL1YCF and contributors — https://github.com/dl1ycf/pihpsdr
- Diagnosis, patch and tools: written with Claude Code.

The patch and tools here are released under the Unlicense, the same as the Radioberry firmware sources. FTDI D2XX is FTDI's own library and is not included; the upstream repo provides it.
