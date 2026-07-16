import type { SVGProps } from "react";

export type IconName =
  | "overview"
  | "search"
  | "performance"
  | "logs"
  | "database"
  | "settings"
  | "refresh"
  | "pause"
  | "play"
  | "download"
  | "chevron"
  | "check"
  | "alert"
  | "menu"
  | "close"
  | "copy"
  | "clock"
  | "memory"
  | "activity"
  | "external";

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

export function Icon({ name, size = 18, ...props }: IconProps) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };
  const paths: Record<IconName, React.ReactNode> = {
    overview: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </>
    ),
    performance: (
      <>
        <path d="M3 17h3l3-6 4 3 4-8 4 3" />
        <path d="M3 21h18" />
      </>
    ),
    logs: (
      <>
        <path d="M6 3h9l4 4v14H6z" />
        <path d="M14 3v5h5M9 12h6M9 16h6" />
      </>
    ),
    database: (
      <>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
        <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.9l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.9-.34 1.7 1.7 0 0 0-1 1.55V21h-4v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.9.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3v-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.9l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3h4v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.9-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 1.55 1H21v4h-.09a1.7 1.7 0 0 0-1.55 1Z" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7v5h-5" />
        <path d="M4.7 9a8 8 0 0 1 13-3L20 8" />
        <path d="M4 17v-5h5" />
        <path d="M19.3 15a8 8 0 0 1-13 3L4 16" />
      </>
    ),
    pause: (
      <>
        <path d="M9 5v14M15 5v14" />
      </>
    ),
    play: <path d="m8 5 11 7-11 7Z" />,
    download: (
      <>
        <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
      </>
    ),
    chevron: <path d="m9 18 6-6-6-6" />,
    check: <path d="m5 12 4 4L19 6" />,
    alert: (
      <>
        <path d="M10.3 3.6 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z" />
        <path d="M12 9v4M12 17h.01" />
      </>
    ),
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    copy: (
      <>
        <rect x="8" y="8" width="11" height="11" rx="2" />
        <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    memory: (
      <>
        <rect x="6" y="5" width="12" height="14" rx="2" />
        <path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M3 9h3M18 9h3M3 15h3M18 15h3" />
      </>
    ),
    activity: <path d="M3 12h4l2-6 4 12 2-6h6" />,
    external: (
      <>
        <path d="M14 4h6v6M20 4l-9 9" />
        <path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" />
      </>
    ),
  };
  return <svg {...common}>{paths[name]}</svg>;
}

export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <path d="M20 2 35.6 11v18L20 38 4.4 29V11Z" fill="#08a9c9" />
      <path d="m20 7 11.3 6.5v13L20 33 8.7 26.5v-13Z" fill="#0d62d8" />
      <path d="m20 7 11.3 6.5L20 20 8.7 13.5Z" fill="#78e5f2" />
      <path d="m20 20 11.3-6.5v13L20 33Z" fill="#063d9c" />
      <path d="m20 20-11.3-6.5v13L20 33Z" fill="#0b7ee8" />
    </svg>
  );
}
