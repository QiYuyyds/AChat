// Base URL for the backend API. Empty string = same-origin (the default, used
// when the Next.js app and API are served from the same host). Set
// NEXT_PUBLIC_API_BASE_URL to point the React frontend at the standalone Python
// FastAPI backend, e.g. http://localhost:8000
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? ''
