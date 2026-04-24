import logging
from typing import Any, Dict, List
from http import HTTPStatus
import httpx

from .exceptions import JIRAApiError, JIRAAuthenticationError
from .field.provider import jira_field_provider
from .jql import JIRAQueryBuilder

logger = logging.getLogger(__name__)

class JIRARestClient:
    """
    Implementation of the JIRARestClient interface using the Jira REST API.
    Handles authentication and API calls to Jira.
    """

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        api_token: str,
        jira_api_version: str = "2",
        verify_ssl: bool = False,
        timeout: float = 30.0,
    ):
        """
        Initialize the Jira client.

        Args:
            base_url: Base URL of the Jira instance (e.g., 'https://jira.mycompany.com')
            auth_token: Personal Access Token (PAT) for authentication
        """
        self.base_url = base_url
        self.auth_token = auth_token
        self.api_token = api_token
        self.verify_ssl = verify_ssl
        self.headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        self.timeout = timeout
        self.jira_api_version = jira_api_version # or settings.JIRA_API_VERSION
        self.query_builder = JIRAQueryBuilder()

    def _get_headers(self) -> Dict[str, str]:
        """
        Private helper to get PAT token and headers for JIRA API requests.
        """
        pat_token = self.api_token
        return {
            "Authorization": f"Bearer {pat_token}",
            "Content-Type": "application/json",
        }

    async def _authenticate(self) -> None:
        """
        Verify authentication with Jira using the provided PAT token.

        Raises:
            httpx.HTTPError: if authentication fails
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/api/{self.jira_api_version}/myself",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Authentication failed: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAAuthenticationError("Error connecting to JIRA")
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Error connecting to JIRA",
            )

    async def _get_selected_fields(self):
        fields = [
            "summary",
            "description",
            "status",
            "created",
            "updated",
            "reporter",
            "project",
            "priority",
            "issuetype",
            "issuelinks",
        ]

        business_analyst_field = jira_field_provider.get_custom_field_id(
            "business_analyst"
        )
        project_pcode_field = jira_field_provider.get_custom_field_id("project_pcode")
        application_id_field = jira_field_provider.get_custom_field_id("application_id")
        success_criteria_field = jira_field_provider.get_custom_field_id(
            "success_criteria"
        )
        epic_link_field = jira_field_provider.get_custom_field_id("epic_link")
        feature_field = jira_field_provider.get_custom_field_id("feature")
        quality_analyst_field = jira_field_provider.get_custom_field_id(
            "quality_analyst"
        )
        acceptance_criteria_field = jira_field_provider.get_custom_field_id(
            "acceptance_criteria"
        )
        product_team_field = jira_field_provider.get_custom_field_id("product_team")
        sprint_field = jira_field_provider.get_custom_field_id("sprint")

        fields.extend(
            [
                business_analyst_field,
                project_pcode_field,
                application_id_field,
                success_criteria_field,
                acceptance_criteria_field,
                epic_link_field,
                feature_field,
                quality_analyst_field,
                product_team_field,
                sprint_field,
            ]
        )

        fields = [f for f in fields if f]
        return fields

    async def get_issues(
        self, jql: str, start_at: int = 0, max_results: int = 50, fields: List[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a JQL query to retrieve issues from Jira.

        Args:
            jql: The JQL query string to execute
            start_at: Index of the first issue to return (pagination)
            max_results: Maximum number of issues to return (pagination)
            fields: Optional list of field names to retrieve. If None, uses default fields from _get_selected_fields()

        Returns:
            Dict[str, Any]: {"issues": [...], "total": int}

        Raises:
            httpx.HTTPError: If the API request fails
        """
        if fields is None:
            fields = await self._get_selected_fields()

        try:
            url = f"{self.base_url}/rest/api/{self.jira_api_version}/search"
            params = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": ",".join(fields),
            }
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                result = response.json()
                return {
                    "issues": result.get("issues", []),
                    "total": result.get("total", None),
                }
        except httpx.HTTPStatusError as e:
            try:
                error_detail = e.response.json()
                error_message = error_detail.get("errorMessages", [e.response.text])[0]
            except Exception:
                error_message = e.response.text
            logger.error(
                f"Error retrieving issues: {e.response.status_code} - {error_message}"
            )
            raise JIRAApiError(status_code=e.response.status_code, detail=error_message)
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Unexpected error retrieving issues: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Error fetching from JIRA"
            )

    async def get_fields(self) -> List[Dict[str, Any]]:
        """
        Retrieve all fields from JIRA using the PAT token.

        Returns:
            List[Dict[str, Any]]: List of field definitions from JIRA
        """
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.get(
                    f"{self.base_url}/rest/api/{self.jira_api_version}/field",
                    headers=self._get_headers(),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Error fetching JIRA fields: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching fields: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e)
            )

    async def create_issue(self, fields: Dict) -> dict:
        """
        Create a new issue in JIRA.

        Args:
            fields: The fields dictionary for the JIRA issue payload

        Returns:
            dict: The created JIRA issue response
        """
        payload = {"fields": fields}

        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.post(
                    f"{self.base_url}/rest/api/{self.jira_api_version}/issue",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Error creating JIRA issue: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Unexpected error creating JIRA issue: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e)
            )

    async def create_issue_link(
        self, inward_issue_id: str, outward_issue_id: str, link_type: str = "Implement"
    ) -> None:
        """
        Create a link between two JIRA issues.

        Args:
            inward_issue_id: ID of the inward issue (usually the child/story)
            outward_issue_id: ID of the outward issue (usually the parent/epic)
            link_type: Type of link to create (default: "Implement")
        """
        payload = {
            "type": {"name": link_type},
            "inwardIssue": {"id": inward_issue_id},
            "outwardIssue": {"id": outward_issue_id},
        }

        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.post(
                    f"{self.base_url}/rest/api/{self.jira_api_version}/issueLink",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Error creating JIRA issue link: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Unexpected error creating JIRA issue link: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e)
            )

    async def update_issue(self, issue_id: str, payload: Dict):
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/issue/{issue_id}"
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.put(
                    url,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as e:
            logger.error(
                f"JIRA update failed: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code,
                detail=f"JIRA update failed: {e.response.text}",
            )
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Unexpected error during JIRA update: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error during JIRA update: {str(e)}",
            )

    async def get_issue_by_key(self, issue_key: str, fields: List[str] = None) -> dict:
        """
        Fetch a single JIRA issue by its key or ID.

        Args:
            issue_key: The JIRA issue key or ID
            fields: Optional list of field names to retrieve. If None, uses default fields from _get_selected_fields()

        Returns:
            dict: The JIRA issue data or None if not found
        """
        if fields is None:
            fields = await self._get_selected_fields()
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/issue/{issue_key}?fields={','.join(fields)}"
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers(),
                    timeout=self.timeout,
                )
                if response.status_code == HTTPStatus.NOT_FOUND:
                    logger.warning(f"Issue not found: {issue_key}")
                    return None
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"JIRA API error: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Error getting JIRA issue by key: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e)
            )

    async def fetch_project_fields(self, project_key: str, issue_type_id: str) -> dict:
        """
        Fetch project fields by its key or ID.
        """
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/issue/createmeta/{project_key}/issuetypes/{issue_type_id}"
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers(),
                    timeout=self.timeout,
                )
                if response.status_code == HTTPStatus.NOT_FOUND:
                    logger.warning(f"Project not found: {project_key}")
                    return None
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"JIRA API error: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error("JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Error fetching project fields: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e)
            )

    async def validate_user_project_access(
        self, project_key: str, username: str
    ) -> bool:
        """
        Validates if a user has access to a specific project.

        Args:
            project_key: The project key or ID
            username: The user's JIRA ID or username

        Returns:
            bool: True if user has access, False otherwise

        Raises:
            CustomException: If the API request fails
        """
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/user/assignable/search?project={project_key}&username={username}"
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers(),
                    timeout=self.timeout,
                )

                if response.status_code == HTTPStatus.NOT_FOUND:
                    logger.warning(
                        f"No project with the provided key: {project_key} exists"
                    )
                    raise JIRAApiError(
                        status_code=HTTPStatus.NOT_FOUND,
                        detail=f"No project with the provided key: {project_key} exists",
                    )

                response.raise_for_status()
                users = response.json()
                # If the response is empty list, user doesn't have access
                return len(users) > 0

        except httpx.HTTPStatusError as e:
            logger.error(
                f"JIRA API error while validating user access: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error(f"JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Error validating user project access: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail=f"Error validating user project access: {str(e)}",
            )

    async def fetch_issue_types(self) -> dict:
        """
        Fetch issue types from JIRA.
        """
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/issuetype"
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers(),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"JIRA API error: {e.response.status_code} - {e.response.text}"
            )
            raise JIRAApiError(
                status_code=e.response.status_code, detail=e.response.text
            )
        except httpx.TimeoutException:
            logger.error(f"JIRA API request timed out")
            raise JIRAApiError(
                status_code=HTTPStatus.GATEWAY_TIMEOUT,
                detail="JIRA API request timed out."
            )
        except Exception as e:
            logger.error(f"Error fetching JIRA issue types: {str(e)}")
            raise JIRAApiError(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e)
            )
