import { useLayoutEffect, useRef, useState } from "react";

export type SegmentOption<T extends string | number> = {
  value: T;
  label: string;
};

type SegmentSliderProps<T extends string | number> = {
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
  className?: string;
};

export function SegmentSlider<T extends string | number>({
  value,
  options,
  onChange,
  className = "",
}: SegmentSliderProps<T>) {
  const trackRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [thumb, setThumb] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const index = Math.max(
      0,
      options.findIndex((option) => option.value === value),
    );
    const button = buttonRefs.current[index];
    const track = trackRef.current;
    if (!button || !track) return;
    const trackBox = track.getBoundingClientRect();
    const buttonBox = button.getBoundingClientRect();
    setThumb({
      left: buttonBox.left - trackBox.left,
      width: buttonBox.width,
    });
  }, [options, value]);

  return (
    <div
      ref={trackRef}
      className={`relative inline-flex rounded-lg bg-muted p-1 ${className}`.trim()}
      role="tablist"
    >
      <span
        aria-hidden
        className="pointer-events-none absolute top-1 h-[calc(100%-8px)] rounded-md bg-background shadow-sm transition-[left,width] duration-200"
        style={{ left: thumb.left, width: thumb.width }}
      />
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            role="tab"
            aria-selected={active}
            ref={(node) => {
              buttonRefs.current[index] = node;
            }}
            onClick={() => onChange(option.value)}
            className={`relative z-10 rounded-md px-3 py-1 text-xs font-medium transition-colors duration-200 ${
              active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
