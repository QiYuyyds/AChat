/** Session Note TypeScript types — mirrors backend SessionNote YAML structure. */

export interface SessionNote {
  title: string
  currentState: string
  keyDecisions: string[]
  filesTouched: string[]
  commandsRun: string[]
  artifactsProduced: string[]
  blockers: string[]
  openQuestions: string[]
  nextSteps: string[]
  architectureUnderstanding: string
}

export interface SessionNoteResponse {
  note: SessionNote | null
  coversUpTo: number | null
}
