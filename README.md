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

### 4. After an unplug, the gateway never recovers (no waterfall until restarted)

When the Juice board is unplugged, or drops off USB because of a power dip, the gateway keeps its handle to the vanished device. When the board comes back it enumerates as a new USB device, but every `FT_Read`/`FT_Write` on the old handle fails at once:

- the gateway never exits, so systemd never restarts it;
- piHPSDR gets no IQ data, so there is no waterfall;
- the gateway spins at 100 % CPU and floods its log with `us stream time out`. On the test PC that was 17 million lines, 327 MB, within minutes.

**Fix:** `stream.c` now exits (code 2) as soon as D2XX reports the device is gone (`FT_DEVICE_NOT_FOUND`), or after 20 stream errors in a row. With `Restart=on-failure` the service restarts the gateway, the start script waits for the board, and the loader reloads and verifies the FPGA.

Tested by resetting the FT2232H with `USBDEVFS_RESET` in the middle of a stream:

```
us stream time out (status 2, 1 in a row)
Radioberry Juice USB stream lost; exiting so the gateway can be restarted.
...
FPGA gateware activated.
frames=3051 (381/s) gap_events=0 lost=0 loss=0.00% bad=0 seq_restarts=0
```

**If the board keeps dropping off USB,** that is a power or cable problem, not software. The kernel log (`sudo journalctl -k`) then shows `usb_submit_urb returned -121` followed by `USB disconnect`, or `device descriptor read/64, error -32` on replug. This was seen on a battery-powered Raspberry Pi CM5 (uConsole) that shared its internal USB hub with other devices. Power the Juice board and Radioberry separately, or through a powered USB hub.

---

## The patches

Both apply to [pa3gsb/Radioberry-2.x](https://github.com/pa3gsb/Radioberry-2.x) at commit `a9c5139` ("gateware selection added....", 2026-09-13):

- [`patches/0001-juice-gateware-loader-linux-d2xx.patch`](patches/0001-juice-gateware-loader-linux-d2xx.patch) changes `juice/firmware-extended/gateware.c` and covers root causes 2 and 3.
- [`patches/0002-juice-stream-exit-on-usb-device-loss.patch`](patches/0002-juice-stream-exit-on-usb-device-loss.patch) changes `juice/firmware-extended/stream.c` and covers root cause 4.

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
2. Applies the patch and builds `juice/firmware-extended` with `linux-Makefile`.
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
- After replugging the board, give the gateway about 15 s to reload the FPGA before starting piHPSDR.

---

## Tools

| File | Purpose |
|---|---|
| `tools/p1test.py` | protocol-1 client: discovery, start, count sequence gaps / bad frames, rough IQ level |
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
