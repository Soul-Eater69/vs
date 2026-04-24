from typing import Dict, Optional
from ..base import JIRAClient
from .base import JIRAFieldProvider
import logging
from .constants import LOGICAL_TO_JIRA_FIELD_NAME, LOGICAL_TO_JIRA_ISSUE_TYPE_NAME

logger = logging.getLogger(__name__)

_JIRA_META_DATA: Optional[Dict[str, str]] = {}

class JIRAFieldDataProvider:
    """
    Provider class for accessing JIRA field data.
    This class caches field data from JIRA to avoid repeated API calls
    and provides access to the field mapping information.
    """

    def __init__(self, jira_client: JIRAClient = None):
        """
        Initialize the provider with a JIRA client.
        Args:
            jira_client: An instance of a class implementing the JIRAClient interface
        """
        self.jira_client = jira_client
        self._custom_field_ids = {}
        self._custom_issue_type_ids = {}

    async def initialize(self) -> None:
        """Initialize field and issue type data from Jira."""
        if not self.jira_client:
            logger.error("Cannot initialize: JIRA client is not set")
            return

        await self._initialize_fields_data()
        await self._initialize_issue_types_data()

    async def _initialize_fields_data(self) -> None:
        """Initialize the fields data from Jira."""
        global _JIRA_META_DATA

        if not self.jira_client:
            logger.error("Cannot initialize: JIRA client is not set")
            return

        try:
            logger.info("Fetching field definitions from JIRA")
            all_fields = await self.jira_client.get_fields()
            _JIRA_META_DATA["fields_data"] = {}
            for logical_key, jira_field_name in LOGICAL_TO_JIRA_FIELD_NAME.items():
                for field in all_fields:
                    if field.get("name") == jira_field_name:
                        _JIRA_META_DATA["fields_data"][logical_key] = field.get("id")
                        break
            logger.info(f"JIRA fields data mapping initialized: {_JIRA_META_DATA}")
            self._custom_field_ids = _JIRA_META_DATA["fields_data"]
        except Exception as e:
            logger.error(f"Failed to initialize JIRA fields data: {str(e)}")
            _JIRA_META_DATA["fields_data"] = {}

    async def _initialize_issue_types_data(self) -> None:
        """Initialize the issue types data from Jira."""
        global _JIRA_META_DATA

        if not self.jira_client:
            logger.error("Cannot initialize: JIRA client is not set")
            return

        try:
            logger.info("Fetching issue types from JIRA")
            all_issue_types = await self.jira_client.fetch_issue_types()
            _JIRA_META_DATA["issue_types"] = {}
            for (
                logical_key,
                jira_issue_type_name,
            ) in LOGICAL_TO_JIRA_ISSUE_TYPE_NAME.items():
                for issue_type in all_issue_types:
                    if issue_type.get("name") == jira_issue_type_name:
                        _JIRA_META_DATA["issue_types"][logical_key] = issue_type.get(
                            "id"
                        )
                        break
            logger.info(f"JIRA issue types mapping initialized: {_JIRA_META_DATA}")
            self._custom_issue_type_ids = _JIRA_META_DATA["issue_types"]
        except Exception as e:
            logger.error(f"Failed to initialize JIRA issue types data: {str(e)}")
            _JIRA_META_DATA["issue_types"] = {}

    async def get_fields_data(self) -> Dict[str, str]:
        """
        Get the JIRA fields data, initializing it if necessary.

        Returns:
            Dict[str, str]: The mapping of logical keys to JIRA custom field IDs
        """
        global _JIRA_META_DATA

        if not (_JIRA_META_DATA and _JIRA_META_DATA.get("fields_data")):
            await self._initialize_fields_data()

        return _JIRA_META_DATA.get("fields_data", {}) or {}

    def get_custom_field_id(self, logical_key: str) -> Optional[str]:
        """
        Get the custom field ID for a given logical key.

        Args:
            logical_key: The logical key (e.g., "project_pcode")

        Returns:
            Optional[str]: The JIRA field ID, or None if not found
        """
        global _JIRA_META_DATA

        if not (
            _JIRA_META_DATA
            and _JIRA_META_DATA.get("fields_data")
            and logical_key in _JIRA_META_DATA["fields_data"]
        ):
            logger.warning(f"No field mapping found for logical key: {logical_key}")
            return None

        return _JIRA_META_DATA["fields_data"].get(logical_key)

    async def get_issue_types(self) -> Dict[str, str]:
        """
        Get the Jira issue types data, initializing it if necessary.

        Returns:
            Dict[str, str]: The mapping of logical keys to JIRA issue type IDs
        """
        global _JIRA_META_DATA

        if not (_JIRA_META_DATA and _JIRA_META_DATA.get("issue_types")):
            await self._initialize_issue_types_data()

        return _JIRA_META_DATA.get("issue_types", {}) or {}

    async def get_issue_type_id(self, logical_key: str) -> Optional[str]:
        """
        Get the issue type ID for a given logical key.

        Args:
            logical_key: The logical key (e.g., "UserStory")

        Returns:
            Optional[str]: The JIRA issue type ID, or None if not found
        """
        global _JIRA_META_DATA
        if not (
            _JIRA_META_DATA
            and _JIRA_META_DATA["issue_types"]
            and logical_key in _JIRA_META_DATA["issue_types"]
        ):
            logger.warning(
                f"No issue type mapping found for logical key: {logical_key}"
            )
            return None

        return _JIRA_META_DATA["issue_types"].get(logical_key)

    def get_linked_epic_key(self, issue: Dict) -> Optional[str]:
        """
        Get the linked epic key from an issue.

        Returns:
            Optional[str]: The linked epic key, or None if not found
        """
        jira_fields = issue.get("fields", {})
        issue_links = jira_fields.get("issuelinks", [])

        linked_epic_key = None
        for i in issue_links:
            if i.get("type", {}).get("outward") == "implements" and "outwardIssue" in i:
                linked_epic_key = i["outwardIssue"].get("key")
                break

        if linked_epic_key is None:
            linked_epic_key = jira_fields.get(
                self.get_custom_field_id("epic_link"), None
            )
        
        return linked_epic_key

jira_field_provider: JIRAFieldProvider = JIRAFieldDataProvider()