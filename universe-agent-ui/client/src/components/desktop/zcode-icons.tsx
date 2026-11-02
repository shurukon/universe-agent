/**
 * zcode-icons.tsx — glyphs copied verbatim from ZCode's bundled icon set
 * (lucide-react v1.17.0, ISC). Path data is byte-identical to what the live
 * ZCode window renders, so stroke geometry matches the reference pixel for
 * pixel at the shared 1.5 stroke width. Do not "upgrade" these by swapping in
 * the local lucide-react package: versions differ and the outlines drift.
 */
import type { ReactNode, SVGProps } from "react";

export type ZcIconProps = SVGProps<SVGSVGElement> & { size?: number };

function Glyph({
  size = 16,
  children,
  ...rest
}: ZcIconProps & { children: ReactNode }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function MessagePlus(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719" />
      <path d="M8 12h8" />
      <path d="M12 8v8" />
    </Glyph>
  );
}

export function SearchIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="m21 21-4.34-4.34" />
      <circle cx="11" cy="11" r="8" />
    </Glyph>
  );
}

export function CalendarClock(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M16 14v2.2l1.6 1" />
      <path d="M16 2v4" />
      <path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5" />
      <path d="M3 10h5" />
      <path d="M8 2v4" />
      <circle cx="16" cy="16" r="6" />
    </Glyph>
  );
}

export function HashIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <line x1="4" x2="20" y1="9" y2="9" />
      <line x1="4" x2="20" y1="15" y2="15" />
      <line x1="10" x2="8" y1="3" y2="21" />
      <line x1="16" x2="14" y1="3" y2="21" />
    </Glyph>
  );
}

export function FolderIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
    </Glyph>
  );
}

export function CollapseAllIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="m14 10 7-7" />
      <path d="M20 10h-6V4" />
      <path d="m3 21 7-7" />
      <path d="M4 14h6v6" />
    </Glyph>
  );
}

export function ListFilterIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M2 5h20" />
      <path d="M6 12h12" />
      <path d="M9 19h6" />
    </Glyph>
  );
}

export function ArchiveIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <rect width="20" height="5" x="2" y="3" rx="1" />
      <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" />
      <path d="M10 12h4" />
    </Glyph>
  );
}

export function PlusIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </Glyph>
  );
}

export function ChevronDownIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="m6 9 6 6 6-6" />
    </Glyph>
  );
}

export function GripIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <circle cx="9" cy="12" r="1" />
      <circle cx="9" cy="5" r="1" />
      <circle cx="9" cy="19" r="1" />
      <circle cx="15" cy="12" r="1" />
      <circle cx="15" cy="5" r="1" />
      <circle cx="15" cy="19" r="1" />
    </Glyph>
  );
}

export function PanelLeftIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
      <path d="m16 15-3-3 3-3" />
    </Glyph>
  );
}

export function ArrowLeftIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="m12 19-7-7 7-7" />
      <path d="M19 12H5" />
    </Glyph>
  );
}

export function ArrowRightIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </Glyph>
  );
}

export function SpinnerIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </Glyph>
  );
}

export function SettingsIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" />
      <circle cx="12" cy="12" r="3" />
    </Glyph>
  );
}

export function EllipsisIcon(props: ZcIconProps) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
      <circle cx="5" cy="12" r="1" />
    </Glyph>
  );
}

/** The app mark the reference window shows in the top-left tile. */
export function ZCodeMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 18 18"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M2.91528 17.32C1.40918 17.32 0.183716 16.0946 0.183716 14.5885V2.91906C0.183716 1.41296 1.40918 0.1875 2.91528 0.1875H14.5847C16.0908 0.1875 17.3163 1.41296 17.3163 2.91906V14.5885C17.3163 16.0946 16.0908 17.32 14.5847 17.32H2.91528Z"
        fill="url(#zc-mark-fill)"
      />
      <path
        d="M14.5847 0.367454C15.9918 0.367454 17.1325 1.50817 17.1325 2.91529V14.5847C17.1325 15.9918 15.9918 17.1325 14.5847 17.1325H2.91529C1.50817 17.1325 0.367454 15.9918 0.367454 14.5847V2.91529C0.367454 1.50817 1.50817 0.367454 2.91529 0.367454H14.5847ZM14.5847 0H2.91529C2.13652 0 1.40459 0.303149 0.853871 0.853871C0.303149 1.40459 0 2.13652 0 2.91529V14.5847C0 15.3635 0.303149 16.0954 0.853871 16.6461C1.40459 17.1969 2.13652 17.5 2.91529 17.5H14.5847C15.3635 17.5 16.0954 17.1969 16.6461 16.6461C17.1969 16.0954 17.5 15.3635 17.5 14.5847V2.91529C17.5 2.13652 17.1969 1.40459 16.6461 0.853871C16.0954 0.303149 15.3635 0 14.5847 0Z"
        fill="#B7BCBF"
      />
      <path
        d="M9.03854 3.86902L8.2306 5.01755C8.1036 5.19645 7.89598 5.306 7.67092 5.306H3.26767V3.86328C3.26767 3.86902 9.03854 3.86902 9.03854 3.86902Z"
        fill="#fff"
      />
      <path
        d="M14.5209 3.86328L7.59573 13.6741H2.97913L9.90426 3.86328H14.5209Z"
        fill="#fff"
      />
      <path
        d="M8.46146 13.6732L9.27514 12.5189C9.40214 12.34 9.60975 12.2305 9.83482 12.2305H14.2323V13.6732H8.46146Z"
        fill="#fff"
      />
      <defs>
        <linearGradient
          id="zc-mark-fill"
          x1="8.74999"
          y1="17.32"
          x2="8.74999"
          y2="0.187504"
          gradientUnits="userSpaceOnUse"
        >
          <stop style={{ stopColor: "#000" }} />
          {/* the reference stop is authored in Display-P3; the sRGB fallback
              renders ~7/255 lighter on the tile, so keep the wide-gamut
              value (Chromium maps it back down on sRGB displays) */}
          <stop
            offset="1"
            stopColor="#151718"
            style={{ stopColor: "color(display-p3 0.0824 0.0902 0.0941)" }}
          />
        </linearGradient>
      </defs>
    </svg>
  );
}
