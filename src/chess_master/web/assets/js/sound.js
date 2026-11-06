/** Zero-dependency Web Audio cues. Every sound is synthesized: no files. */

const STORAGE_KEY = "chessmaster.sound.muted";

function readMuted() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeMuted(value) {
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    // Storage can be blocked in private shells; the toggle still works for this run.
  }
}

function tone(ctx, { at = 0, freq = 440, to = null, dur = 0.12, type = "sine", gain = 0.2 }) {
  const start = ctx.currentTime + at;
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  if (to) osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), start + dur);
  amp.gain.setValueAtTime(0.0001, start);
  amp.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), start + 0.006);
  amp.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  osc.connect(amp).connect(ctx.destination);
  osc.start(start);
  osc.stop(start + dur + 0.03);
}

function noise(ctx, { at = 0, dur = 0.03, gain = 0.2, highpass = 900 }) {
  const start = ctx.currentTime + at;
  const buffer = ctx.createBuffer(1, Math.max(1, Math.floor(ctx.sampleRate * dur)), ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let index = 0; index < data.length; index += 1) data[index] = Math.random() * 2 - 1;
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  const filter = ctx.createBiquadFilter();
  filter.type = "highpass";
  filter.frequency.value = highpass;
  const amp = ctx.createGain();
  amp.gain.setValueAtTime(gain, start);
  amp.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  source.connect(filter).connect(amp).connect(ctx.destination);
  source.start(start);
}

function click(ctx, at, volume, { freq = 190, dur = 0.055, gain = 0.34, noiseGain = 0.22 } = {}) {
  tone(ctx, { at, freq, to: freq * 0.55, dur, type: "triangle", gain: gain * volume });
  noise(ctx, { at, dur: Math.min(dur, 0.028), gain: noiseGain * volume, highpass: 1400 });
}

const VOICES = {
  move: (ctx, volume) => click(ctx, 0, volume),
  capture: (ctx, volume) =>
    click(ctx, 0, volume, { freq: 108, dur: 0.09, gain: 0.46, noiseGain: 0.3 }),
  castle: (ctx, volume) => {
    click(ctx, 0, volume, { freq: 150 });
    click(ctx, 0.085, volume, { freq: 215, gain: 0.26, noiseGain: 0.14 });
  },
  check: (ctx, volume) => {
    tone(ctx, { at: 0, freq: 880, dur: 0.09, type: "square", gain: 0.09 * volume });
    tone(ctx, { at: 0.1, freq: 1175, dur: 0.13, type: "square", gain: 0.08 * volume });
  },
  promote: (ctx, volume) => {
    for (const [index, freq] of [523, 659, 784, 1047].entries()) {
      tone(ctx, { at: index * 0.06, freq, dur: 0.16, type: "triangle", gain: 0.11 * volume });
    }
  },
  brilliant: (ctx, volume) => {
    for (const [index, freq] of [1047, 1319, 1568, 2093].entries()) {
      tone(ctx, { at: index * 0.055, freq, dur: 0.22, type: "sine", gain: 0.085 * volume });
    }
  },
  retry_correct: (ctx, volume) => {
    tone(ctx, { at: 0, freq: 659, dur: 0.12, type: "sine", gain: 0.14 * volume });
    tone(ctx, { at: 0.1, freq: 988, dur: 0.18, type: "sine", gain: 0.12 * volume });
  },
  retry_wrong: (ctx, volume) => {
    tone(ctx, { at: 0, freq: 233, to: 131, dur: 0.28, type: "sawtooth", gain: 0.11 * volume });
  },
  game_over: (ctx, volume) => {
    for (const [index, freq] of [392, 494, 587].entries()) {
      tone(ctx, { at: index * 0.13, freq, dur: 0.5, type: "sine", gain: 0.1 * volume });
    }
  },
  timeout: (ctx, volume) => {
    for (let index = 0; index < 3; index += 1) {
      tone(ctx, { at: index * 0.19, freq: 174, dur: 0.12, type: "square", gain: 0.15 * volume });
    }
  },
};

class SoundManager {
  constructor() {
    this.muted = readMuted();
    this.volume = 0.5;
    this.context = null;
  }

  /** Browsers only allow audio after a gesture: call this from an input event. */
  unlock() {
    if (!this.context) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return;
      try {
        this.context = new Ctor();
      } catch {
        this.context = null;
        return;
      }
    }
    if (this.context.state === "suspended") this.context.resume().catch(() => {});
  }

  setMuted(value) {
    this.muted = Boolean(value);
    writeMuted(this.muted);
  }

  toggle() {
    this.setMuted(!this.muted);
    return this.muted;
  }

  play(name) {
    if (this.muted) return;
    const voice = VOICES[name];
    if (!voice) return;
    this.unlock();
    if (!this.context) return;
    try {
      voice(this.context, this.volume);
    } catch {
      // Audio must never break the board.
    }
  }

  /** Pick the right cue for a played move without any extra backend fields. */
  move(move, status = null) {
    if (!move) return;
    const san = String(move.san || "");
    if (san.startsWith("O-O")) this.play("castle");
    else if (san.includes("=")) this.play("promote");
    else if (move.captured) this.play("capture");
    else this.play("move");
    if (san.includes("+") && !san.includes("#")) {
      window.setTimeout(() => this.play("check"), 110);
    }
    if (status && status.state === "timeout") this.play("timeout");
  }
}

export const sound = new SoundManager();
