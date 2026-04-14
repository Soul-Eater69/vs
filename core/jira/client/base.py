from typing import Any, Dict, List, Protocol

class JIRAClient(Protocol):
    """
    Protocol defining the interface for JIRA service implementations.
    This allows for flexible dependency injection and testing.
    """

    async def get_issues(self, jql: str, start_at: int = 0, max_results: int = 50) -> Dict[str, Any]:
        """
        Execute a JQL query to retrieve issues from Jira.

        Args:
            jql: The JQL query string to execute
            start_at: Index of the first issue to return (pagination)
            max_results: Maximum number of issues to return (pagination)

        Returns:
            Dict[str, Any]: {"issues": [...], "total": int}
        """
        ...

    async def get_fields(self) -> List[Dict[str, Any]]:
        """
        Retrieve all fields from JIRA using the PAT token.
        Returns: List[Dict[str, Any]]: list of field definitions from JIRA
        """
        ...

    async def create_issue(self, fields: dict) -> dict:
        """
        Create a new issue in JIRA.
        Args: fields: The fields dictionary for the JIRA issue payload
        Returns: dict: The created JIRA issue response
        """
        ...

    async def create_issue_link(
        self,
        inward_issue_id: str,
        outward_issue_id: str,
        link_type: str = "Implement"
    ) -> None:
        """
        Create a link between two JIRA issues.

        Args:
            inward_issue_id: ID of the inward issue (usually the child/story)
            outward_issue_id: ID of the outward issue (usually the parent/epic)
            link_type: Type of link to create (default: "Implement")
        """
        ...

    async def update_issue(self, issue_id: str, payload: Dict) -> Any:
        """
        Update a JIRA issue.

        Args:
            issue_id: The JIRA issue ID or key.
            payload: The data to update with

        Returns:
            The JIRA API response.
        """
        ...

    async def get_issue_by_key(self, issue_key: str) -> dict:
        """Fetch a single Jira issue by its key or ID.

        Args:
            issue_key: The key or ID of the issue to fetch

        Returns:
            dict: The Jira issue data
        """
        ...

    async def fetch_project_fields(self, project_key: str, issue_type_id: str) -> dict:
        """
        Get fields for a specific project and issue type.

        Args:
            project_key: The key identifier for the project
            issue_type_id: The ID of the issue type to filter fields

        Returns:
            dict: Field definitions for the project
        """
        ...

    async def validate_user_project_access(self, project_key: str, username: str) -> bool:
        """Validates if a user has access to a specific project.

        Args:
            project_key: The key identifier for the project
            username: The username of the user to validate

        Returns:
            bool: True if the user has access to the project, False otherwise
        """
        ...

    async def fetch_issue_types(self) -> dict:
        """Fetch issue types from Jira.

        Returns:
            dict: Issue types information
        """
        ...