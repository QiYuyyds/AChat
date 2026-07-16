# Desktop Multi-Process Lifecycle

## Purpose

Defines the lifecycle management of the three core processes (Electron Main, Python FastAPI, Next.js Standalone) in the desktop app, including startup ordering, health monitoring, restart policies, and shutdown cleanup.

## Requirements

### Requirement: Processes SHALL start in a defined order

The Electron main process MUST start the Python and Next.js subprocesses in a specific order with health probes before the BrowserWindow is created.

#### Scenario: Normal startup
- **WHEN** Electron main process starts
- **THEN** Python and Next.js subprocesses are spawned in parallel
- **AND** health probes begin for both processes
- **AND** the BrowserWindow is created only after BOTH processes report healthy
- **AND** the BrowserWindow loads the Next.js URL

#### Scenario: Python fails to start within timeout
- **WHEN** the Python subprocess does not respond to health probes within 15 seconds
- **THEN** an error dialog is shown: "Backend service failed to start"
- **AND** the user is offered "Retry" or "Quit"

#### Scenario: Next.js fails to start within timeout
- **WHEN** the Next.js subprocess does not respond to health probes within 15 seconds
- **THEN** an error dialog is shown: "Frontend service failed to start"
- **AND** the user is offered "Retry" or "Quit"

### Requirement: Python subprocess SHALL listen on a random port

To avoid port conflicts, the Python FastAPI server MUST listen on an OS-assigned ephemeral port.

#### Scenario: Python subprocess is spawned
- **WHEN** Electron spawns the Python process
- **THEN** `--host 127.0.0.1 --port 0` is passed to uvicorn
- **OR** a random port in the range 49152-65535 is selected and probed for availability
- **AND** the selected port is passed to the Next.js subprocess as `NEXT_PUBLIC_API_BASE_URL`

### Requirement: Next.js subprocess SHALL listen on a random port

To avoid port conflicts with development environments, the Next.js server MUST listen on a random port.

#### Scenario: Next.js subprocess is spawned
- **WHEN** Electron spawns the Next.js process
- **THEN** `--port <random>` is passed
- **AND** the selected port is used as the BrowserWindow loadURL target

### Requirement: Health probes SHALL monitor subprocesses continuously

The Electron main process MUST periodically check the health of both subprocesses.

#### Scenario: Periodic health check
- **WHEN** the app is running
- **THEN** every 30 seconds, Electron sends `GET /health` to Python and `GET /` to Next.js
- **AND** if either returns a non-200 status or connection fails, a failure counter is incremented

#### Scenario: Consecutive health check failures
- **WHEN** 3 consecutive health checks fail for a subprocess
- **THEN** the subprocess is automatically restarted
- **AND** a notification is shown to the user: "Service restarted"

### Requirement: Crashed subprocesses SHALL be automatically restarted

If a subprocess exits unexpectedly, the Electron main process MUST attempt to restart it.

#### Scenario: Python subprocess crashes
- **WHEN** the Python subprocess exits with a non-zero code
- **THEN** Electron waits 2 seconds and restarts the Python subprocess
- **AND** the BrowserWindow shows a reconnecting overlay

#### Scenario: Restart fails twice in a row
- **WHEN** automatic restart fails for the second consecutive time
- **THEN** an error dialog is shown: "AChat services are experiencing issues. Please restart the application."
- **AND** no further automatic restart attempts are made

### Requirement: Application exit SHALL clean up all subprocesses

When the user closes the application, all subprocesses MUST be terminated gracefully.

#### Scenario: User closes the main window
- **WHEN** the user clicks the window close button
- **THEN** SIGTERM is sent to both Python and Next.js subprocesses
- **AND** Electron waits up to 3 seconds for graceful shutdown
- **AND** if a subprocess has not exited, SIGKILL is sent (on Windows: `taskkill /F /T /PID`)

#### Scenario: User force-quits via task manager
- **WHEN** the user kills the AChat process via the OS task manager
- **THEN** subprocesses may become orphaned
- **AND** on next launch, Electron detects orphaned processes by checking PID files and terminates them

### Requirement: PID files SHALL be written for subprocess tracking

Each subprocess MUST write its PID to a file for orphan detection on next launch.

#### Scenario: Python subprocess starts
- **WHEN** the Python subprocess starts successfully
- **THEN** its PID is written to `<userData>/data/.python.pid`

#### Scenario: Next.js subprocess starts
- **WHEN** the Next.js subprocess starts successfully
- **THEN** its PID is written to `<userData>/data/.nextjs.pid`

#### Scenario: Orphan process detected on next launch
- **WHEN** AChat starts and a `.python.pid` file exists
- **THEN** Electron reads the PID and checks if the process is still running
- **AND** if it is running, it is terminated before starting a new subprocess
