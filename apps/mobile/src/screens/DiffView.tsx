import { diffSign, type DiffLine } from "../lib/diff"

/** Renders pre-computed diff lines with +/- coloring. Shared by approvals and diff artifacts. */
export function DiffView({ lines }: { lines: DiffLine[] }) {
  if (lines.length === 0) {
    return (
      <div className="diff-view">
        <span className="diff-line">（无改动）</span>
      </div>
    )
  }

  return (
    <div className="diff-view">
      {lines.map((line, index) => (
        <span key={index} className={lineClass(line.kind)}>
          {line.kind === "hunk" ? line.text : `${diffSign(line.kind)} ${line.text}`}
        </span>
      ))}
    </div>
  )
}

function lineClass(kind: DiffLine["kind"]): string {
  return kind === "context" ? "diff-line" : `diff-line ${kind}`
}
