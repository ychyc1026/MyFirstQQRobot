import { useId, useRef, useState } from "react";

type FilePickerProps = {
  accept?: string;
  label?: string;
  placeholder?: string;
  onChange: (file: File | null) => void;
  className?: string;
};

export function FilePicker({
  accept,
  label,
  placeholder = "未选择文件",
  onChange,
  className = "",
}: FilePickerProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");

  return (
    <div className={className}>
      {label ? (
        <label htmlFor={inputId} className="mb-1 block text-xs text-muted-foreground">
          {label}
        </label>
      ) : null}
      <div className="flex items-center gap-3 rounded-2xl bg-muted/50 px-3 py-2.5">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="shrink-0 rounded-md bg-primary px-4 py-2 text-xs font-bold text-primary-foreground transition hover:opacity-90"
        >
          选择文件
        </button>
        <span className={`min-w-0 flex-1 truncate text-sm ${name ? "text-foreground" : "text-muted-foreground"}`}>
          {name || placeholder}
        </span>
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          accept={accept}
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            setName(file?.name ?? "");
            onChange(file);
          }}
        />
      </div>
    </div>
  );
}
