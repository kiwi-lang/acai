/**
 * PCM audio player that queues base64-encoded int16 chunks and plays
 * them back seamlessly using the Web Audio API.
 */

export interface AudioChunk {
    pcm_base64: string;
    sample_rate: number;
    sample_width: number;
    channels: number;
}

export type PlayerState = 'idle' | 'playing' | 'paused';

export interface AudioProgress {
    elapsed: number;
    total: number;
    fraction: number;
}

type StateCallback = (state: PlayerState) => void;
type ProgressCallback = (progress: AudioProgress) => void;

export class AudioPlayer {
    private ctx: AudioContext | null = null;
    private gainNode: GainNode | null = null;
    private queue: AudioBuffer[] = [];
    private scheduledSources: AudioBufferSourceNode[] = [];
    private nextTime = 0;
    private state: PlayerState = 'idle';
    private onStateChange: StateCallback | null = null;
    private onProgress: ProgressCallback | null = null;
    private _volume = 1.0;

    private playbackStartCtxTime = 0;
    private totalDuration = 0;
    private progressTimer: ReturnType<typeof setInterval> | null = null;

    constructor(onStateChange?: StateCallback, onProgress?: ProgressCallback) {
        this.onStateChange = onStateChange ?? null;
        this.onProgress = onProgress ?? null;
    }

    private getCtx(): AudioContext {
        if (!this.ctx) {
            this.ctx = new AudioContext();
            this.gainNode = this.ctx.createGain();
            this.gainNode.gain.value = this._volume;
            this.gainNode.connect(this.ctx.destination);
        }
        return this.ctx;
    }

    private getGain(): GainNode {
        this.getCtx();
        return this.gainNode!;
    }

    enqueue(chunk: AudioChunk): void {
        const ctx = this.getCtx();
        const raw = atob(chunk.pcm_base64);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);

        const int16 = new Int16Array(bytes.buffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

        const buffer = ctx.createBuffer(chunk.channels, float32.length, chunk.sample_rate);
        buffer.getChannelData(0).set(float32);
        this.queue.push(buffer);

        if (this.state !== 'paused') {
            this.scheduleNext();
        }
    }

    private scheduleNext(): void {
        const ctx = this.getCtx();
        const gain = this.getGain();
        if (this.queue.length === 0) return;

        if (this.state !== 'playing') {
            this.setState('playing');
            this.nextTime = ctx.currentTime;
            this.playbackStartCtxTime = ctx.currentTime;
            this.startProgressTimer();
        }

        while (this.queue.length > 0) {
            const buffer = this.queue.shift()!;
            const source = ctx.createBufferSource();
            source.buffer = buffer;
            source.connect(gain);

            const startAt = Math.max(this.nextTime, ctx.currentTime);
            source.start(startAt);
            this.nextTime = startAt + buffer.duration;
            this.totalDuration = this.nextTime - this.playbackStartCtxTime;
            this.scheduledSources.push(source);

            source.onended = () => {
                const idx = this.scheduledSources.indexOf(source);
                if (idx >= 0) this.scheduledSources.splice(idx, 1);

                if (this.scheduledSources.length === 0 && this.queue.length === 0 && this.state === 'playing') {
                    this.emitProgress();
                    this.setState('idle');
                }
            };
        }
    }

    pause(): void {
        if (this.state !== 'playing' || !this.ctx) return;
        this.ctx.suspend();
        this.stopProgressTimer();
        this.setState('paused');
    }

    resume(): void {
        if (this.state !== 'paused' || !this.ctx) return;
        this.ctx.resume();
        this.startProgressTimer();
        this.setState('playing');
        this.scheduleNext();
    }

    stop(): void {
        this.stopProgressTimer();
        for (const src of this.scheduledSources) {
            try { src.stop(); } catch { /* already stopped */ }
        }
        this.scheduledSources = [];
        if (this.ctx) {
            this.ctx.close();
            this.ctx = null;
            this.gainNode = null;
        }
        this.queue = [];
        this.nextTime = 0;
        this.totalDuration = 0;
        this.setState('idle');
    }

    setVolume(v: number): void {
        this._volume = Math.max(0, Math.min(1, v));
        if (this.gainNode) {
            this.gainNode.gain.value = this._volume;
        }
    }

    get volume(): number {
        return this._volume;
    }

    get currentState(): PlayerState {
        return this.state;
    }

    get isPlaying(): boolean {
        return this.state === 'playing';
    }

    getProgress(): AudioProgress {
        if (!this.ctx || this.totalDuration <= 0) {
            return { elapsed: 0, total: 0, fraction: 0 };
        }
        const elapsed = Math.min(
            this.ctx.currentTime - this.playbackStartCtxTime,
            this.totalDuration,
        );
        return {
            elapsed,
            total: this.totalDuration,
            fraction: Math.min(elapsed / this.totalDuration, 1),
        };
    }

    private emitProgress(): void {
        this.onProgress?.(this.getProgress());
    }

    private startProgressTimer(): void {
        this.stopProgressTimer();
        this.progressTimer = setInterval(() => this.emitProgress(), 250);
    }

    private stopProgressTimer(): void {
        if (this.progressTimer !== null) {
            clearInterval(this.progressTimer);
            this.progressTimer = null;
        }
    }

    private setState(s: PlayerState): void {
        if (this.state === s) return;
        this.state = s;
        if (s === 'idle') this.stopProgressTimer();
        this.onStateChange?.(s);
    }
}
