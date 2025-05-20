"""
Manager for handling analysis feedback data.
This manager uses a separate SQLite database to store feedback data.
"""

import logging
import os
import sqlite3
from datetime import datetime
from typing import (
    Dict,
    Optional,
    Union,
)

from galaxy.config import GalaxyAppConfiguration

log = logging.getLogger(__name__)

# Schema based on specified requirements
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analysis_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    feedback BOOLEAN NOT NULL,
    comment TEXT,
    dataset_id TEXT,
    created_at TEXT NOT NULL
);
"""


class AnalysisFeedbackManager:
    """
    Manager class for handling analysis feedback data in a separate SQLite database.
    """

    def __init__(self, config: GalaxyAppConfiguration):
        self.config = config
        # Default to a file in Galaxy's data directory if not specified in config
        self.db_path = getattr(
            config, "analysis_feedback_db_path", os.path.join(config.data_dir, "analysis_feedback.db")
        )
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(CREATE_TABLE_SQL)
            conn.commit()
            conn.close()
            log.info(f"Analysis feedback database initialized at {self.db_path}")
        except Exception as e:
            log.error(f"Failed to initialize analysis feedback database: {e}")
            raise

    def save_feedback(self, data: Dict) -> int:
        """
        Save feedback data to the SQLite database.

        :param data: Dictionary containing feedback data
        :return: ID of the inserted record
        """
        try:
            # Validate required fields
            if not all(key in data for key in ["question", "answer", "feedback"]):
                raise ValueError("Missing required fields: question, answer, feedback")

            # Convert feedback to boolean if needed
            if isinstance(data["feedback"], str):
                data["feedback"] = data["feedback"].lower() in ("true", "t", "1", "yes", "y")

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Insert data
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """
                INSERT INTO analysis_feedback
                (question, answer, feedback, comment, dataset_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data["question"],
                    data["answer"],
                    data["feedback"],
                    data.get("comment", None),
                    data.get("dataset_id", None),
                    now,
                ),
            )

            inserted_id = cursor.lastrowid
            conn.commit()
            conn.close()

            log.info(f"Saved analysis feedback with ID: {inserted_id}")
            return inserted_id

        except Exception as e:
            log.error(f"Failed to save analysis feedback: {e}")
            raise

    def get_feedback(self, feedback_id: int) -> Optional[Dict]:
        """
        Retrieve a specific feedback entry by ID.

        :param feedback_id: ID of the feedback entry
        :return: Dictionary with feedback data or None if not found
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM analysis_feedback WHERE id = ?", (feedback_id,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(row)
            return None

        except Exception as e:
            log.error(f"Failed to retrieve analysis feedback: {e}")
            raise
