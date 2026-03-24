"""Face recognition system — learn and identify Discord users from photos."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "faces"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FACE_DB = DATA_DIR / "face_db.json"

# Face database: {discord_user_id: {"name": str, "encodings": [list of face encoding arrays]}}
_db = {}


def _load():
    global _db
    if FACE_DB.exists():
        try:
            _db = json.loads(FACE_DB.read_text())
        except Exception:
            _db = {}


def _save():
    try:
        FACE_DB.write_text(json.dumps(_db, indent=2))
    except Exception:
        pass


def register_face(user_id: str, user_name: str, image_path: str) -> bool:
    """Register a face from an image file for a Discord user.
    Uses a simple hash-based approach since we don't have dlib/face_recognition installed.
    Stores the avatar/image reference for the LLM to compare visually.
    """
    if user_id not in _db:
        _db[user_id] = {"name": user_name, "images": []}

    _db[user_id]["name"] = user_name
    _db[user_id]["images"].append(image_path)
    # Keep last 5 reference images per user
    _db[user_id]["images"] = _db[user_id]["images"][-5:]
    _save()
    return True


def get_registered_users() -> dict:
    """Get all registered users."""
    return {uid: info["name"] for uid, info in _db.items()}


def get_user_images(user_id: str) -> list[str]:
    """Get reference images for a user."""
    if user_id not in _db:
        return []
    return _db[user_id].get("images", [])


def get_roster() -> str:
    """Get a text summary of known faces."""
    if not _db:
        return "No faces registered yet."
    lines = ["Known faces:"]
    for uid, info in _db.items():
        lines.append(f"- {info['name']} ({len(info.get('images', []))} reference photos)")
    return "\n".join(lines)


# Load on import
_load()
