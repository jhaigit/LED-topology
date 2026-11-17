// A simulated Arduino Uno for the ltp_stream_serial firmware: ATmega328P at
// 16 MHz with its timers, UART and EEPROM, plus probes on the LED pins.

'use strict';

const fs = require('fs');
const {
  CPU, avrInstruction, AVRTimer, timer0Config, timer1Config, timer2Config,
  AVRUSART, usart0Config, AVRIOPort, portBConfig, portCConfig, portDConfig,
  AVREEPROM, EEPROMMemoryBackend, eepromConfig,
} = require('avr8js');

const F_CPU = 16000000;
const FLASH_BYTES = 0x8000;
const EEPROM_BYTES = 1024;

function loadHex(text, target) {
  for (const line of text.split('\n')) {
    if (line[0] !== ':' || line.substr(7, 2) !== '00') continue;
    const length = parseInt(line.substr(1, 2), 16);
    const address = parseInt(line.substr(3, 4), 16);
    for (let i = 0; i < length; i++) {
      target[address + i] = parseInt(line.substr(9 + i * 2, 2), 16);
    }
  }
}

class Board {
  /**
   * @param hexPath firmware image
   * @param pins {wsPort: 'B', wsBit: 0, apaPort: 'D', apaData: 3, apaClock: 2}
   */
  constructor(hexPath, pins) {
    const program = new Uint16Array(FLASH_BYTES / 2);
    loadHex(fs.readFileSync(hexPath, 'utf8'), new Uint8Array(program.buffer));
    this.cpu = new CPU(program);
    this.timers = [
      new AVRTimer(this.cpu, timer0Config),
      new AVRTimer(this.cpu, timer1Config),
      new AVRTimer(this.cpu, timer2Config),
    ];
    this.ports = {
      B: new AVRIOPort(this.cpu, portBConfig),
      C: new AVRIOPort(this.cpu, portCConfig),
      D: new AVRIOPort(this.cpu, portDConfig),
    };
    this.eeprom = new AVREEPROM(this.cpu, new EEPROMMemoryBackend(EEPROM_BYTES), eepromConfig);
    this.usart = new AVRUSART(this.cpu, usart0Config, F_CPU);

    this.fromDevice = [];   // bytes the firmware sent
    this.toDevice = [];     // bytes waiting to be clocked into its UART
    this.usart.onByteTransmit = (value) => this.fromDevice.push(value);

    // avr8js clears the receive-complete flag on every write to UCSR0B and
    // with it loses a byte that has arrived but not been read. The ATmega328P
    // clears that flag only when UDR0 is read or the receiver is switched off
    // (datasheet, "USART Control and Status Register n A", bit RXCn). The
    // firmware writes UCSR0B at the start and end of every WS2812 frame to
    // mask the receive interrupt, so put the flag back as the chip would.
    const UCSR0A = usart0Config.UCSRA;
    const UCSR0B = usart0Config.UCSRB;
    const RXC0 = 0x80;
    const RXEN0 = 0x10;
    const RXCIE0 = 0x80;
    const writeUcsrb = this.cpu.writeHooks[UCSR0B];
    this.cpu.writeHooks[UCSR0B] = (value, oldValue, address, mask) => {
      const pending = (this.cpu.data[UCSR0A] & RXC0) && (value & RXEN0) && (oldValue & RXEN0);
      const result = writeUcsrb(value, oldValue, address, mask);
      if (pending) {
        this.cpu.data[UCSR0A] |= RXC0;
        // With the interrupt enabled, an unread byte interrupts at once.
        if (value & RXCIE0) this.cpu.queueInterrupt(this.usart.RXC);
      }
      return result;
    };

    // Receive overruns: a byte finished arriving while the one before it was
    // still unread. The simulated UART holds one byte where the real one
    // holds two, so firmware that never overruns here has margin on hardware.
    this.uartOverruns = [];  // cycle counts
    let unread = false;
    const UDR0 = usart0Config.UDR;
    const readUdr = this.cpu.readHooks[UDR0];
    this.cpu.readHooks[UDR0] = (address) => {
      unread = false;
      return readUdr(address);
    };
    this.usart.onRxComplete = () => {
      if (unread) this.uartOverruns.push(this.cpu.cycles);
      unread = true;
    };

    // WS2812 probe: every edge on the data pin, with its cycle count.
    this.wsEdges = [];      // [cycle, level]
    const wsMask = 1 << pins.wsBit;
    this.ports[pins.wsPort].addListener((value, oldValue) => {
      if ((value ^ oldValue) & wsMask) {
        this.wsEdges.push([this.cpu.cycles, (value & wsMask) ? 1 : 0]);
      }
    });

    // APA102 probe: the data bit at every rising clock edge.
    this.apaBits = [];      // [cycle, bit]
    const clockMask = 1 << pins.apaClock;
    const dataMask = 1 << pins.apaData;
    this.ports[pins.apaPort].addListener((value, oldValue) => {
      if ((value & clockMask) && !(oldValue & clockMask)) {
        this.apaBits.push([this.cpu.cycles, (value & dataMask) ? 1 : 0]);
      }
    });
  }

  get cycles() { return this.cpu.cycles; }

  /** Queue bytes for the firmware's UART; they arrive at the baud rate. */
  send(bytes) { this.toDevice.push(...bytes); }

  /** Run for a number of CPU cycles. */
  run(cycles) {
    const end = this.cpu.cycles + cycles;
    const cpu = this.cpu;
    const usart = this.usart;
    while (cpu.cycles < end) {
      if (this.toDevice.length && !usart.rxBusy) {
        usart.writeByte(this.toDevice.shift());
      }
      avrInstruction(cpu);
      cpu.tick();
    }
  }

  runMs(ms) { this.run(Math.round(ms * F_CPU / 1000)); }

  /** Run until predicate() is true; false if it took longer than timeoutMs. */
  runUntil(predicate, timeoutMs) {
    const end = this.cpu.cycles + timeoutMs * F_CPU / 1000;
    while (this.cpu.cycles < end) {
      if (predicate()) return true;
      this.run(2000);
    }
    return predicate();
  }

  /** Read RAM, e.g. a firmware variable located through the ELF symbols. */
  ram(address, length) {
    return Array.from(this.cpu.data.slice(address, address + length));
  }

  /** Overwrite one byte of RAM. */
  poke(address, value) {
    this.cpu.data[address] = value;
  }
}

module.exports = { Board, F_CPU };
