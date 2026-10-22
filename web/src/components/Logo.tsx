// Знак «Symposium»: круглый стол и пять мест. По кругу медленно движется
// подсвеченное место — слово переходит от участника к участнику.
// `live` — анимация идёт (симпозиум говорит); иначе знак неподвижен.
export function Logo({ size = 28, live = false, seat = 0 }: { size?: number; live?: boolean; seat?: number }) {
  const seats = Array.from({ length: 5 }, (_, i) => {
    const a = (-90 + i * 72) * (Math.PI / 180);
    return { x: 32 + 22 * Math.cos(a), y: 32 + 22 * Math.sin(a), i };
  });
  return (
    <svg className={`logo ${live ? "live" : ""}`} width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Symposium">
      <circle cx="32" cy="32" r="14" fill="none" stroke="var(--accent)" strokeWidth="3.2" />
      {seats.map((s) => (
        <circle key={s.i} cx={s.x} cy={s.y} r="4.2" className={`seat s${s.i}`} fill={!live && s.i === seat ? "var(--accent)" : "currentColor"} />
      ))}
    </svg>
  );
}
export const logoCss = `
.logo { color: var(--ink); flex: none; }
.logo .seat { opacity: .9; }
.logo.live .seat { animation: seat 5s linear infinite; }
.logo.live .s1 { animation-delay: 1s; } .logo.live .s2 { animation-delay: 2s; } .logo.live .s3 { animation-delay: 3s; } .logo.live .s4 { animation-delay: 4s; }
@keyframes seat { 0%, 20% { fill: var(--accent); r: 5.4px; } 21%, 100% { fill: currentColor; r: 4.2px; } }
`;
