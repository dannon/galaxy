import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from galaxy_test.base.populators import (
    DatasetPopulator,
    skip_without_tool,
)
from galaxy_test.driver import integration_util


class TestAnalysisFeedbackAPI(integration_util.IntegrationTestCase):
    dataset_populator: DatasetPopulator

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        # Use a temporary file for the analysis feedback database
        cls.temp_db_file = tempfile.mktemp(suffix=".db")
        config["analysis_feedback_db_path"] = cls.temp_db_file
        return config

    def setUp(self):
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)
        # Setup a user for authentication
        self.galaxy_interactor.api_test_interactor.setup_user()

    @classmethod
    def tearDownClass(cls):
        # Clean up the temporary database file
        if os.path.exists(cls.temp_db_file):
            os.unlink(cls.temp_db_file)
        super().tearDownClass()

    def test_analysis_feedback_endpoint(self):
        # Test basic feedback submission
        feedback_data = {
            "question": "Test question",
            "answer": "Test answer",
            "feedback": True,
            "comment": "Test comment",
            "dataset_id": "test_id",
        }

        # Send feedback to the API
        response = self.galaxy_interactor.post("/api/chat/analysis_feedback", data=feedback_data, json=True)

        # Check response
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["status"] == "success"
        assert "feedback_id" in response_data

        # Verify data was correctly stored in the SQLite database
        with closing(sqlite3.connect(self.temp_db_file)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM analysis_feedback WHERE id = ?", (response_data["feedback_id"],))
            row = cursor.fetchone()

            assert row is not None
            assert row["question"] == "Test question"
            assert row["answer"] == "Test answer"
            assert row["feedback"] == 1  # SQLite stores booleans as integers
            assert row["comment"] == "Test comment"
            assert row["dataset_id"] == "test_id"

    def test_analysis_feedback_missing_fields(self):
        # Test with missing required fields
        incomplete_data = {
            "question": "Test question",
            # Missing "answer" field
            "feedback": True,
        }

        # Send incomplete data to the API
        response = self.galaxy_interactor.post("/api/chat/analysis_feedback", data=incomplete_data, json=True)

        # With FastAPI validation, missing required field should return 422 Unprocessable Entity
        assert response.status_code == 422

    def test_analysis_feedback_boolean_values(self):
        # Test with different valid boolean values
        data = {"question": "Test question", "answer": "Test answer", "feedback": True}  # Boolean true

        # Print request details for debugging
        print(f"API URL: {self.galaxy_interactor.url}/api/chat/analysis_feedback")
        print(f"Request data: {data}")

        # Send data to the API
        response = self.galaxy_interactor.post("/api/chat/analysis_feedback", data=data, json=True)

        # Should work with a boolean value
        print(f"Response status: {response.status_code}")
        print(f"Response content: {response.content.decode('utf-8')}")
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code} with content: {response.content.decode('utf-8')}"
        response_data = response.json()
        assert response_data["status"] == "success", f"Expected status success, got: {response_data}"

        # Verify in database
        with closing(sqlite3.connect(self.temp_db_file)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM analysis_feedback WHERE id = ?", (response_data["feedback_id"],))
            row = cursor.fetchone()

            assert row is not None
            assert row["feedback"] == 1  # True stored as 1
