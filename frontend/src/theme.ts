export const colors = {
  // Text hierarchy
  textPrimary:   "#111827",   // headings, primary content
  textSecondary: "#374151",   // body text, table data
  textMuted:     "#6b7280",   // timestamps, subtitles
  textFaint:     "#9ca3af",   // placeholders, role labels

  // Surfaces
  white:         "#ffffff",
  bgPage:        "#f9fafb",   // app background
  bgLight:       "#f3f4f6",   // table headers, subtle dividers

  // Borders
  border:        "#e5e7eb",   // card borders, dividers
  borderMuted:   "#d1d5db",   // input borders, secondary buttons

  // Brand – indigo
  brand:         "#6366f1",   // active accent, links
  brandDark:     "#4338ca",   // ingest notice title
  brandBorder:   "#c7d2fe",   // ingest notice border, live message highlight
  brandBg:       "#eef2ff",   // ticker badge bg, ingest notice bg
  brandActiveBg: "#f0f4ff",   // active sidebar item bg
  brandBgSoft:   "#f5f3ff",   // live/pending message bubble bg

  // Status
  success:       "#10b981",   // done icon, completion text
  warning:       "#f59e0b",   // running / in-progress icon

  // Error – red
  error:         "#ef4444",   // error icon
  errorText:     "#b91c1c",   // error message text
  errorBg:       "#fef2f2",   // error toast background
  errorBorder:   "#fecaca",   // error toast border (red-200)
  errorDivider:  "#fee2e2",   // subtle error divider in timeline (red-100)
} as const;
