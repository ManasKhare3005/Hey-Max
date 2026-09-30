import type { ReactNode } from "react";

/** Tiny Markdown renderer for Max's notes: headings, bullets, **bold**, *italic*.
 * Builds React elements (no innerHTML), so transcript text can never inject markup. */
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|“[^”]+”)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={`${key}b${i++}`}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("*")) out.push(<em key={`${key}i${i++}`}>{t.slice(1, -1)}</em>);
    else out.push(<q key={`${key}q${i++}`}>{t.slice(1, -1)}</q>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: ReactNode[] = [];
  const flush = (k: number) => {
    if (bullets.length) blocks.push(<ul key={`ul${k}`}>{bullets}</ul>);
    bullets = [];
  };
  text.split("\n").forEach((raw, n) => {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*]\s+(.*)/);
    if (bullet) {
      bullets.push(<li key={`li${n}`}>{inline(bullet[1], `l${n}`)}</li>);
      return;
    }
    flush(n);
    if (!line.trim()) return;
    if (line.startsWith("### ")) blocks.push(<h5 key={n}>{inline(line.slice(4), `h${n}`)}</h5>);
    else if (line.startsWith("## ")) blocks.push(<h4 key={n}>{inline(line.slice(3), `h${n}`)}</h4>);
    else if (line.startsWith("# ")) blocks.push(<h3 key={n}>{inline(line.slice(2), `h${n}`)}</h3>);
    else blocks.push(<p key={n}>{inline(line, `p${n}`)}</p>);
  });
  flush(-1);
  return <div className="md">{blocks}</div>;
}
