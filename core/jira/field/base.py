from typing import Dict, Protocol, Optional


class JIRAFieldProvider(Protocol):
    """Protocol defining the interface for Jira field providers."""

    async def initialize(self) -> None:
        """Initialize field and issue type data from Jira."""
        ...

    async def get_fields_data(self) -> Dict[str, str]:
        """Get the Jira fields data, initializing it if necessary."""
        ...

    def get_custom_field_id(self, logical_key: str) -> Optional[str]:
        """Get the custom field ID for a logical key."""
        ...

    async def get_issue_types(self) -> Dict[str, str]:
        """Get the Jira issue types data, initializing it if necessary."""
        ...

    async def get_issue_type_id(self, logical_key: str) -> Optional[str]:
        """Get the issue type ID for a given logical key."""
        ...

    def get_linked_epic_key(self, issue: Dict) -> Optional[str]:
        """Get the linked epic key from an issue."""
        ...