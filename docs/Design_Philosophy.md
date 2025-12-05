# Prompt Valet Design Philosophy (Seed Version)

Prompt Valet is built around a few core principles:

## 1. Determinism
A prompt dropped into the inbox should always produce the same output when run under the same conditions.  
Automations must behave predictably.

## 2. Transparency
Users should always be able to see:
- What happened
- Why it happened
- What Prompt Valet decided to do

Logs and folder structures must reflect reality.

## 3. Local-First Control
Prompt Valet runs fully on the user's own infrastructure.  
No cloud dependencies.  
No external state outside the user's repos and inbox.

## 4. Stable Filesystem Contracts
Tools that rely on Prompt Valet expect:
- inbox and processed folders
- a predictable config file
- stable repo paths

Future components (installer, TUI) will rely on this stability.

## 5. Minimal Surprise
Prompt Valet should never:
- Delete repos
- Rewrite history unexpectedly
- Move files silently without logging
- Execute prompts that the user didn't explicitly drop in

## 6. Extensible Without Fragility
The system should allow:
- Installers
- TUIs
- Alternative file servers
- Plugin-like extensions
without breaking the core architecture.

This is the conceptual north star for Phase 1 and beyond.
