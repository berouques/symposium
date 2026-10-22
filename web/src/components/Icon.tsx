const P: Record<string, string> = {
  plus: "M12 5v14M5 12h14", play: "M7 4l12 8-12 8z", pause: "M8 5v14M16 5v14", stop: "M6 6h12v12H6z",
  send: "M4 12l16-8-6 17-3-7z", folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  file: "M6 3h8l4 4v14H6zM14 3v4h4", terminal: "M4 6l6 6-6 6M12 18h8", book: "M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h10",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0", sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19",
  moon: "M20 14.5A8 8 0 1 1 9.5 4 6.5 6.5 0 0 0 20 14.5z", chev: "M6 9l6 6 6-6", chevr: "M9 6l6 6-6 6", check: "M5 12l5 5 9-10", circle: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z",
  alert: "M12 4l9 16H3zM12 10v4M12 17v.5", settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .5-1.5-1.5.5-2-3-1-1 2h-2l-1-2-3 1 .5 2L6 8.5 4 8l-1 3 2 1v1l-2 1 1 3 2-.5 1.5 1.5-.5 2 3 1 1-2h2l1 2 3-1-.5-2 1.5-1.5 2 .5 1-3-2-1z",
  panel: "M4 5h16v14H4zM14 5v14", download: "M12 4v11M7 11l5 5 5-5M5 20h14", upload: "M12 16V5M7 9l5-5 5 5M5 20h14", trash: "M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18", code: "M9 8l-5 4 5 4M15 8l5 4-5 4M13 5l-2 14", hand: "M8 12V6a1.5 1.5 0 0 1 3 0v5M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6c-3 0-4-1.5-6-4l-1-2 2-1 2 2",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5", scale: "M12 4v16M6 20h12M5 7h14M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z", x: "M6 6l12 12M18 6L6 18",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6", quote: "M7 7h4v5H7zM13 7h4v5h-4zM7 12c0 3 1 4 3 5M13 12c0 3 1 4 3 5", logout: "M10 5H5v14h5M14 8l4 4-4 4M18 12H9",
  copy: "M9 9h10v11H9zM5 15V4h10", paperclip: "M20 11l-8.5 8.5a5 5 0 0 1-7-7L13 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L14 7", users: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2 20a7 7 0 0 1 14 0M16 4.5a3.5 3.5 0 0 1 0 6.5M22 20a6 6 0 0 0-4-5.6", edit: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", cpu: "M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4",
};
export function Icon({ n, size = 16 }: { n: keyof typeof P | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[n] || P.circle} />
    </svg>
  );
}
