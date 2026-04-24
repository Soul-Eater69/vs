from typing import Any, Dict, List, Optional
from .base import JIRAClient

class JIRAMockClient(JIRAClient):
    """Mock implementation of the JiraClient for testing and development."""

    def __init__(self, mock_data: dict = None):
        self.mock_data = mock_data or self._default_mock_data()

    def _default_mock_data(self) -> Dict[str, Any]:
        """Provides default mock data if none is provided."""
        return {
            "issues": [
                {
                    "id": "IDP-1",
                    "key": "IDP-1",
                    "fields": {
                        "summary": "Sample Issue 1",
                        "description": "This is a mock issue for testing",
                        "status": {"name": "In Progress"},
                        "issuetype": {"name": "Story"},
                        "priority": {"name": "High"},
                        "assignee": {"displayName": "John Doe"},
                        "customfield_10001": "EPIC-1"  # Epic Link field
                    }
                },
                {
                    "id": "IDP-2",
                    "key": "IDP-2",
                    "fields": {
                        "summary": "Sample Issue 2",
                        "description": "Another mock issue",
                        "status": {"name": "To Do"},
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "Medium"},
                        "assignee": {"displayName": "Jane Smith"},
                    }
                }
            ],
            "fields": [
                {
                    "id": "customfield_10001",
                    "name": "Epic Link",
                    "custom": True,
                },
                {
                    "id": "customfield_10002",
                    "name": "Story Points",
                    "custom": True,
                }
            ],
            "issue_types": [
                {"id": "1", "name": "Story"},
                {"id": "2", "name": "Bug"},
                {"id": "3", "name": "Epic"},
                {"id": "4", "name": "Task"}
            ],
            "project_fields": {
                "IDP": {
                    "fields": [
                        {"id": "summary", "name": "Summary"},
                        {"id": "description", "name": "Description"},
                        {"id": "customfield_10001", "name": "Epic Link"}
                    ]
                }
            }
        }

    async def get_issues(
        self,
        jql: str,
        start_at: int = 0,
        max_results: int = 50
    ) -> Dict[str, Any]:
        """Mock implementation of get_issues."""
        total = len(self.mock_data["issues"])
        end_at = min(start_at + max_results, total)
        return {
            "issues": self.mock_data["issues"][start_at:end_at],
            "total": total,
            "startAt": start_at,
            "maxResults": max_results
        }

    async def get_fields(self) -> List[Dict[str, Any]]:
        """Mock implementation of get_fields."""
        return self.mock_data["fields"]

    async def create_issue(self, fields: dict) -> dict:
        """Mock implementation of create_issue."""
        new_issue = {
            "id": f"IDP-{len(self.mock_data['issues']) + 1}",
            "key": f"IDP-{len(self.mock_data['issues']) + 1}",
            "fields": fields
        }
        self.mock_data["issues"].append(new_issue)
        return new_issue

    async def create_issue_link(
        self,
        inward_issue_id: str,
        outward_issue_id: str,
        link_type: str = "Implement"
    ) -> None:
        """Mock implementation of create_issue_link."""
        # In mock implementation, we'll just pass as success
        pass

    async def update_issue(self, issue_id: str, payload: Dict) -> Any:
        """Mock implementation of update_issue."""
        for issue in self.mock_data["issues"]:
            if issue["id"] == issue_id or issue["key"] == issue_id:
                issue["fields"].update(payload)
                return issue
        return None

    async def get_issue_by_key(self, issue_key: str) -> dict:
        """Mock implementation of get_issue_by_key."""
        for issue in self.mock_data["issues"]:
            if issue["key"] == issue_key:
                return issue
        return None

    async def fetch_project_fields(self, project_key: str, issue_type_id: str) -> dict:
        """Mock implementation of fetch_project_fields."""
        return self.mock_data["project_fields"].get(project_key, {"fields": []})

    async def validate_user_project_access(self, project_key: str, username: str) -> bool:
        """Mock implementation of validate user project access."""
        # In mock implementation, always return True
        return True

    async def fetch_issue_types(self) -> dict:
        """Mock implementation of fetch_issue_types."""
        return self.mock_data["issue_types"]