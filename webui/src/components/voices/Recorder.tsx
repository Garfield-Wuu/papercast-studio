import { useEffect, useRef, useState } from "react";
import { Mic, Square, RotateCcw, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

const MAX_DURATION_MS = 5 * 60 * 1000; // MiniMax accepts up to ~5 min for cloning samples
const WAVEFORM_FFT_SIZE = 2048;

interface Props {
  onComplete: (file: File, durationMs: number) => void;
  /** Force-clear the recorded blob from outside (e.g. wizard reset). */
  resetSignal?: number;
}

type State = "idle" | "recording" | "finished" | "error";

/**
 * In-browser recorder for the voice-clone wizard.
 *
 *   getUserMedia → MediaRecorder (audio/webm; codecs=opus)
 *   AudioContext → AnalyserNode → canvas waveform (rAF)
 *   5-minute hard cap, then auto-stop.
 *
 * The recorded blob is wrapped in a `File` object before handing off so
 * the upload code path stays identical to the file-picker branch. The
 * server-side `/voice/clone` route accepts `.webm` and transcodes via
 * ffmpeg before forwarding to MiniMax.
 */
export function Recorder({ onComplete, resetSignal }: Props) {
  const [state, setState] = useState<State>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(0);
  const tickIntervalRef = useRef<number | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // External reset (e.g. wizard "重新开始")
  useEffect(() => {
    if (resetSignal != null) {
      cleanup();
      setState("idle");
      setElapsed(0);
      setAudioUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setErrorMsg(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetSignal]);

  // Always clean up on unmount.
  useEffect(() => {
    return () => {
      cleanup();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cleanup = () => {
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    if (tickIntervalRef.current) {
      window.clearInterval(tickIntervalRef.current);
      tickIntervalRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      try { recorderRef.current.stop(); } catch { /* ignore */ }
    }
    recorderRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      audioContextRef.current.close().catch(() => undefined);
    }
    audioContextRef.current = null;
    analyserRef.current = null;
  };

  const startRecording = async () => {
    setErrorMsg(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setErrorMsg("浏览器不支持录音；请使用 Chrome/Edge/Firefox 最新版，或选「上传文件」。");
      setState("error");
      return;
    }
    if (typeof MediaRecorder === "undefined") {
      setErrorMsg("浏览器缺少 MediaRecorder 支持；请改用「上传文件」。");
      setState("error");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Pick a mime type the browser actually supports. Chromium gives
      // webm/opus; Safari only mp4/aac. Server transcodes either to mp3.
      const mime = pickMimeType();
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
        const ext = (mime?.includes("mp4") ? "mp4" : "webm") as "mp4" | "webm";
        const file = new File([blob], `recording.${ext}`, { type: blob.type });
        const url = URL.createObjectURL(blob);
        setAudioUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
        const duration = Date.now() - startTimeRef.current;
        setState("finished");
        cleanup();
        onComplete(file, duration);
      };

      // Audio analysis for waveform.
      const ctx = new (window.AudioContext || (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext!)();
      audioContextRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = WAVEFORM_FFT_SIZE;
      source.connect(analyser);
      analyserRef.current = analyser;

      recorder.start(250); // emit data every 250ms; smoothes blob accumulation
      startTimeRef.current = Date.now();
      setState("recording");
      setElapsed(0);

      // Draw loop
      drawWaveform();

      // Tick loop for elapsed time + 5-min hard stop
      tickIntervalRef.current = window.setInterval(() => {
        const ms = Date.now() - startTimeRef.current;
        setElapsed(ms);
        if (ms >= MAX_DURATION_MS) {
          stopRecording();
        }
      }, 100);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErrorMsg(`无法访问麦克风：${msg}`);
      setState("error");
      cleanup();
    }
  };

  const stopRecording = () => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop(); // triggers onstop → cleanup + onComplete
    }
  };

  const restart = () => {
    cleanup();
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    setState("idle");
    setElapsed(0);
    setErrorMsg(null);
  };

  const drawWaveform = () => {
    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    if (!canvas || !analyser) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const bufferLength = analyser.fftSize;
    const dataArray = new Uint8Array(bufferLength);

    const draw = () => {
      animationFrameRef.current = requestAnimationFrame(draw);
      analyser.getByteTimeDomainData(dataArray);

      const w = canvas.width;
      const h = canvas.height;
      // Use the resolved CSS variables so the waveform follows the theme.
      const styles = getComputedStyle(canvas);
      const accent = styles.getPropertyValue("--color-accent").trim() || "#4f46e5";
      const surface = styles.getPropertyValue("--color-surface-2").trim() || "#1f2937";

      ctx.fillStyle = surface;
      ctx.fillRect(0, 0, w, h);

      ctx.lineWidth = 2;
      ctx.strokeStyle = accent;
      ctx.beginPath();
      const sliceWidth = w / bufferLength;
      let x = 0;
      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * h) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += sliceWidth;
      }
      ctx.lineTo(w, h / 2);
      ctx.stroke();
    };
    draw();
  };

  const remaining = Math.max(0, MAX_DURATION_MS - elapsed);
  const remainingSec = Math.ceil(remaining / 1000);
  const closeToCap = remaining < 30 * 1000 && state === "recording";

  return (
    <div className="space-y-3">
      {errorMsg && (
        <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger flex items-start gap-2">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      <canvas
        ref={canvasRef}
        width={800}
        height={120}
        className="block w-full h-24 rounded border border-border bg-surface-2"
      />

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className={cn("text-sm font-mono", closeToCap ? "text-warning" : "text-fg-muted")}>
          {state === "recording"
            ? `录制中 · ${formatDuration(elapsed)} / ${formatDuration(MAX_DURATION_MS)}`
            : state === "finished"
              ? `已录制 ${formatDuration(elapsed)}`
              : "未开始"}
          {closeToCap && ` · ${remainingSec}s 后自动停止`}
        </div>
        <div className="flex items-center gap-2">
          {state === "idle" && (
            <Button variant="primary" size="sm" onClick={startRecording}>
              <Mic size={14} /> 开始录音
            </Button>
          )}
          {state === "recording" && (
            <Button variant="primary" size="sm" onClick={stopRecording}>
              <Square size={14} /> 停止
            </Button>
          )}
          {(state === "finished" || state === "error") && (
            <Button variant="secondary" size="sm" onClick={restart}>
              <RotateCcw size={14} /> 重录
            </Button>
          )}
        </div>
      </div>

      {audioUrl && state === "finished" && (
        <audio src={audioUrl} controls className="w-full" />
      )}
    </div>
  );
}

function pickMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  for (const c of candidates) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) return c;
  }
  return "";
}

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
