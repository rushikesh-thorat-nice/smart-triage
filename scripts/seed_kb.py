"""Seed the KB into Chroma + SQLite. Run once before starting the server (optional — the app auto-seeds on startup if empty)."""
from app import kb


if __name__ == "__main__":
    n = kb.seed_from_file()
    print(f"Seeded {n} KB entries into Chroma + SQLite.")
