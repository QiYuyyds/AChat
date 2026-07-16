# Desktop Python Runtime

## Purpose

Defines how AChat Desktop packages, distributes, and manages the embedded Python runtime and its dependencies. The Python process runs the full FastAPI backend locally, enabling bash/fs tool execution on the user's machine.

## Requirements

### Requirement: Python runtime SHALL be embedded in the installation package

The desktop app MUST include a Python 3.11 embeddable distribution so that users do not need to pre-install Python.

#### Scenario: User installs AChat Desktop on Windows
- **WHEN** the user installs AChat
- **THEN** `python/python.exe` and `python/python311.dll` exist in the app directory
- **AND** the Python version is 3.11.x

#### Scenario: User installs AChat Desktop on macOS
- **WHEN** the user installs AChat
- **THEN** `python/bin/python3.11` exists in the app directory
- **AND** `python/lib/libpython3.11.dylib` exists

### Requirement: Python dependencies SHALL be pre-downloaded as wheel files

All Python package dependencies MUST be pre-downloaded as `.whl` files during the build process and included in the installation package, enabling fully offline installation.

#### Scenario: Build process collects wheels
- **WHEN** `pnpm electron:build` runs
- **THEN** `pip download -r backend/requirements.txt -d dist/wheels/ --only-binary=:all:` is executed
- **AND** all wheels for the target platform are present in `dist/wheels/`

#### Scenario: User has no internet during first launch
- **WHEN** the user starts AChat for the first time without internet
- **THEN** `python -m pip install --no-index --find-links=wheels/ -r requirements.txt` succeeds
- **AND** all dependencies are installed from local wheel files

### Requirement: First launch SHALL install Python dependencies automatically

The desktop app MUST detect whether Python site-packages are installed and run `pip install` on first launch.

#### Scenario: First launch with no site-packages
- **WHEN** AChat starts and `<userData>/data/.pip_installed` does not exist
- **THEN** the app displays a progress indicator
- **AND** runs `pip install --no-index --find-links=<appRoot>/wheels/ -r <appRoot>/backend/requirements.txt`
- **AND** writes `.pip_installed` with the requirements hash upon success

#### Scenario: Subsequent launch skips installation
- **WHEN** AChat starts and `.pip_installed` exists with matching requirements hash
- **THEN** `pip install` is skipped
- **AND** the Python subprocess starts immediately

### Requirement: Python subprocess SHALL be spawned with correct environment

The Electron main process MUST spawn the Python uvicorn subprocess with all necessary environment variables from `server.json` and local paths.

#### Scenario: Python subprocess starts
- **WHEN** Electron spawns the Python process
- **THEN** the following environment variables are set:
  - `DATABASE_URL` from server.json
  - `MILVUS_HOST` from server.json
  - `MILVUS_PORT` from server.json
  - `ES_ADDRESSES` from server.json
  - `NEO4J_URI` from server.json
  - `NEO4J_USER` from server.json
  - `NEO4J_PASSWORD` from server.json
  - `REDIS_URL` from server.json
  - `PHOENIX_ENDPOINT` from server.json
  - `AGENTHUB_DATA_DIR` set to `<userData>/data`
  - `HOST=127.0.0.1`
  - `PORT=<random>`
  - `PYTHONPATH=<appRoot>/backend`
  - `JWT_SECRET` from `<userData>/data/jwt_secret.txt`

#### Scenario: JWT_SECRET is generated on first launch
- **WHEN** AChat starts for the first time
- **AND** `<userData>/data/jwt_secret.txt` does not exist
- **THEN** a random 48-character secret is generated
- **AND** written to `jwt_secret.txt`
- **AND** set as `JWT_SECRET` env var for the Python process

### Requirement: Python dependency upgrades SHALL be detected on app update

When the desktop app is updated and `requirements.txt` has changed, the app MUST re-install Python dependencies.

#### Scenario: App updated with new requirements
- **WHEN** AChat starts after an update
- **AND** the hash in `.pip_installed` does not match the current `requirements.txt`
- **THEN** `pip install` runs again with the new wheels
- **AND** `.pip_installed` is updated with the new hash

### Requirement: Wheels directory MAY be deleted after installation

After Python dependencies are successfully installed, the `wheels/` directory occupies unnecessary disk space.

#### Scenario: User clicks "Free disk space" in settings
- **WHEN** the user chooses to free disk space
- **THEN** the `wheels/` directory is deleted
- **AND** the app continues to function normally (site-packages already installed)
