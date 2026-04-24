"""Constants for Jira field names and types."""

LOGICAL_TO_JIRA_FIELD_NAME = {
    "story_type": "Story Type",
    "project_pcode": "Project P-Code",
    "application_id": "Application ID",
    "business_analyst": "Business Analyst",
    "success_criteria": "Success Criteria",
    "assignee": "Assignee",
    "epic_link": "Epic Link",
    "parent_link": "Parent Link",  # portfolio/hierarchy parent (customfield_11401)
    "feature": "Feature",
    "quality_analyst": "Quality Analyst",
    "acceptance_criteria": "Acceptance Criteria",
    "product_team": "Product Team",
    "sprint": "Sprint",
}

LOGICAL_TO_JIRA_ISSUE_TYPE_NAME = {
    "ProductModification": "Product Modification",
    "UserStory": "Story",
    "Epic": "Epic",
}