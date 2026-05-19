import json
import os
import time

FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'feedback.json')

def save_feedback(session_id: str, rating: int, comment: str = ""):
    feedback_entry = {
        "session_id": session_id,
        "rating": rating,
        "comment": comment,
        "timestamp": time.time()
    }

    try:
        feedback_path = os.path.abspath(FEEDBACK_FILE)
        if os.path.exists(feedback_path):
            with open(feedback_path, 'r') as f:
                data = json.load(f)
        else:
            data = []

        data.append(feedback_entry)

        with open(feedback_path, 'w') as f:
            json.dump(data, f, indent=2)

        return True
    except Exception as e:
        print(f"Error saving feedback: {e}")
        return False

def get_feedback_summary():
    try:
        feedback_path = os.path.abspath(FEEDBACK_FILE)
        if not os.path.exists(feedback_path):
            return {"count": 0, "average": 0}

        with open(feedback_path, 'r') as f:
            data = json.load(f)

        if not data:
            return {"count": 0, "average": 0}

        ratings = [entry["rating"] for entry in data if "rating" in entry]
        return {
            "count": len(ratings),
            "average": round(sum(ratings) / len(ratings), 1)
        }
    except Exception as e:
        print(f"Error reading feedback: {e}")
        return {"count": 0, "average": 0}
