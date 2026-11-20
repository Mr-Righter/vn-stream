import { useEffect, useRef, useState } from 'react';
import { Button, Select, Slider, Typography } from 'antd';
import { Music, Pause, Play, Repeat, RotateCcw, RotateCw, Volume2 } from 'lucide-react';
import { t } from '../../ui/preferences-store.js';
import { bytes } from '../../ui/format.js';
const clock = (n: number) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
export function AudioPlayer({
  src,
  name,
  size,
  onError,
}: {
  src: string;
  name: string;
  size: number;
  onError: () => void;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [loop, setLoop] = useState(false),
    [rate, setRate] = useState(1),
    [volume, setVolume] = useState(1);
  const [deviceVolume, setDeviceVolume] = useState(
    /iPhone|iPad|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1),
  );
  useEffect(() => {
    const element = audio.current;
    return () => {
      element?.pause();
      element?.removeAttribute('src');
      element?.load();
    };
  }, [src]);
  const seek = (value: number) => {
    if (audio.current && duration > 0) {
      audio.current.currentTime = Math.min(duration, Math.max(0, value));
      setTime(audio.current.currentTime);
    }
  };
  return (
    <div className="vn-audio-card">
      <audio
        ref={audio}
        src={src}
        preload="metadata"
        loop={loop}
        onError={onError}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) =>
          setDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)
        }
      />
      <div className="vn-audio-identity">
        <span className="vn-audio-icon">
          <Music size={28} />
        </span>
        <div>
          <Typography.Text strong>{name}</Typography.Text>
          <Typography.Text type="secondary">{bytes(size)}</Typography.Text>
        </div>
      </div>
      <Slider
        min={0}
        max={duration || 1}
        step={0.1}
        value={time}
        disabled={!duration}
        ariaLabelForHandle={t('media.seek')}
        tooltip={{ formatter: (value) => clock(value ?? 0) }}
        onChange={seek}
      />
      <div className="vn-media-time">
        <span>{clock(time)}</span>
        <span>{clock(duration)}</span>
      </div>
      <div className="vn-audio-controls">
        <Button
          type={loop ? 'primary' : 'text'}
          icon={<Repeat size={19} />}
          aria-pressed={loop}
          aria-label={t('media.loop')}
          onClick={() => setLoop(!loop)}
        />
        <Button
          type="text"
          icon={<RotateCcw size={19} />}
          aria-label={t('media.backward')}
          onClick={() => seek(time - 10)}
        >
          <span className="vn-skip-seconds">10</span>
        </Button>
        <Button
          type="primary"
          shape="circle"
          size="large"
          icon={playing ? <Pause size={24} /> : <Play size={24} />}
          aria-label={t(playing ? 'media.pause' : 'media.play')}
          onClick={() => {
            if (playing) audio.current?.pause();
            else void audio.current?.play().catch(() => {});
          }}
        />
        <Button
          type="text"
          icon={<RotateCw size={19} />}
          aria-label={t('media.forward')}
          onClick={() => seek(time + 10)}
        >
          <span className="vn-skip-seconds">10</span>
        </Button>
        <Select
          aria-label={t('media.speed')}
          value={rate}
          options={[0.5, 1, 1.25, 1.5, 2].map((value) => ({ value, label: `${value}×` }))}
          onChange={(value) => {
            setRate(value);
            if (audio.current) audio.current.playbackRate = value;
          }}
        />
      </div>
      <div className="vn-audio-volume">
        <Volume2 size={18} aria-hidden="true" />
        {deviceVolume ? (
          <Typography.Text type="secondary">{t('media.deviceVolume')}</Typography.Text>
        ) : (
          <Slider
            min={0}
            max={1}
            step={0.01}
            value={volume}
            ariaLabelForHandle={t('media.volume')}
            tooltip={{ formatter: (v) => `${Math.round((v ?? 0) * 100)}%` }}
            onChange={(value) => {
              if (audio.current) {
                audio.current.volume = value;
                if (Math.abs(audio.current.volume - value) > 0.01) setDeviceVolume(true);
                else setVolume(value);
              }
            }}
          />
        )}
      </div>
    </div>
  );
}
