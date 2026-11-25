# LTP Stream (ESP32) - pattern-only LED controller on WiFi

Firmware for the ESP32 family that joins the network as an LTP sink but takes
no pixel stream. The device shows its own patterns, chosen and tuned through
controls. Every pixel is calculated while the frame is being sent, so there
is no frame buffer.

Patterns and controls are listed in
[libraries/LtpStreamFx/README.md](../libraries/LtpStreamFx/README.md).
The serial version, for Arduino, Teensy 3.2 and Teensy 4.0, is `ltp_stream_serial`.

**This sketch compiles for ESP32 and ESP32-C3 but has not been run on
hardware.** The pattern engine and the control table are the code the tests
cover; the RMT output, WiFi and JSON handling here are untested. The
networking, auth and pairing code is taken from `ltp_esp32c3_oled`.

## Hardware

| | ESP32 | ESP32-C3 and others |
| --- | --- | --- |
| WS2812 data | GPIO 16 | GPIO 3 |
| APA102 / SK9822 | the board's default SPI pins (`MOSI`, `SCK`), 4 MHz | same |
| Largest `num_pixels` | 4000 | 4000 |

Pins are in `config.h` and can be overridden at build time
(`-DWS2812_PIN=...`). Only one output is active at a time, selected by the
`led_type` control.

## Build and upload

```bash
make setup                  # ESP32 core and ArduinoJson, first time only
make build                  # ESP32
make build-c3               # ESP32-C3 with native USB
make upload PORT=/dev/ttyUSB0
```

Needs Arduino core 3.x for ESP32: the WS2812 output uses the RMT "simple
encoder" of ESP-IDF 5.3 and later.

## Setting it up

The patterns run from power-up, without WiFi. Over the USB console:

```
wifi <ssid> <password>
save
reboot
```

Other console commands: `info`, `controls`, `get <control>`,
`set <control> <value>`, `name <device_name>`, `reset`, and for device auth
`auth-key`, `auth-off`, `pair` (the pairing PIN is printed on the console).
`help` lists them.

## On the network

- Advertised as `_ltp-sink._tcp` on port 5000, with the strip's size and
  shape in the TXT records; these follow the `num_pixels` and `matrix_width`
  controls.
- `capability_request` returns the device description and the controls;
  `control_get` and `control_set` work as on any sink. Control ids are the
  control names (`local_mode`, `speed`, ...).
- `stream_setup` and `stream_control` are answered with
  `"status": "error", "error": "NOT_SUPPORTED"`, which the controller reports
  as a failed route. The capability response carries `"pixel_input": false`.
- Device auth (claim) and X25519 + PIN pairing as in `ltp_esp32c3_oled`.
  With auth enabled, `control_set` needs a valid claim.

## How the frame is sent

**WS2812:** the RMT peripheral sends the frame. An encoder callback, called
by the RMT driver from its interrupt whenever it has room, calculates one
pixel and writes that pixel's 24 symbols. Neither the pixels nor the RMT
symbols (96 bytes per pixel) are ever held for the whole strip. 30 us per
pixel.

The callback has to refill the RMT memory before it empties. Two memory
blocks are requested to give other interrupts (WiFi) time to finish; if
frames glitch under heavy WiFi traffic, that is the place to look
(`RMT_MEM_SYMBOLS` in `led_output.h`).

**APA102:** SPI, sent 16 pixels at a time from a 64-byte buffer on the stack.

## Settings

Stored in NVS (`ltp_stream` namespace) by the `save` control or command:
the pattern settings, WiFi credentials and the auth key. `reset` restores the
pattern settings and keeps WiFi and auth.
