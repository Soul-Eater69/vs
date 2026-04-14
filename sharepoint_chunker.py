import json
import os
import re
import traceback
from copy import deepcopy
from typing import Any, Dict, List, Optional, Union

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import ClientSecretCredential
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from delia.data_processing.chunking import BaseDocumentChunker
from delia.utils.utils import create_logger, load_config


class MarkdownMetadataExtractor:
    """
    Extract metadata from markdown content containing HTML tables and comments.

    Parameters
    ----------
    markdown_content : str
        Markdown content with embedded HTML tables and comments

    Attributes
    ----------
    markdown_content : str
        Cleaned markdown content with HTML comments removed
    tables : list
        List of HTML table strings found in the content
    soup_tables : list
        List of BeautifulSoup objects for each table
    """

    def __init__(self, markdown_content):
        self.markdown_content = markdown_content
        self.tables = re.findall(r"(<table>.*?</table>)", markdown_content, re.DOTALL)
        self.soup_tables = [
            BeautifulSoup(table, 'html.parser') for table in self.tables
        ]
        self.markdown_content = self.clean_markdown_content(self.markdown_content)

    def clean_markdown_content(self, markdown_content):
        """
        Removes page header, page footer, and page break HTML comments from markdown content.

        Parameters
        ----------
        markdown_content : str
            Markdown content with HTML comments to be cleaned

        Returns
        -------
        str
            Cleaned markdown content with HTML comments removed
        """
        # Remove cleaned = re.sub(r"", "", markdown_content)
        # Remove cleaned = re.sub(r"", "", cleaned)
        # Remove cleaned = re.sub(r"", "", cleaned)
        return cleaned

    def extract_key_value_pairs(self, soup):
        """
        Extract key-value pairs from HTML table rows.

        Parameters
        ----------
        soup : BeautifulSoup
            BeautifulSoup object containing the HTML table

        Returns
        -------
        dict
            Dictionary of key-value pairs extracted from the table
        """
        pairs = {}
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            # Check if all <td> cells have a colon with text on both sides
            valid_row = all(
                [
                    ":" in cell.get_text(separator=" ", strip=True)
                    and cell.get_text(separator=" ", strip=True)
                    .split(":", 1)[0]
                    .strip()
                    and cell.get_text(separator=" ", strip=True)
                    .split(":", 1)[1]
                    .strip()
                    for cell in cells
                ]
            )
            if valid_row and cells:
                for cell in cells:
                    text = cell.get_text(separator=" ", strip=True)
                    key, value = text.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    pairs[key] = value
            elif len(cells) == 2:
                key = cells[0].get_text(separator=" ", strip=True).strip(":").strip()
                value = cells[1].get_text(separator=" ", strip=True).strip()
                if key and value:
                    pairs[key] = value
        return pairs

    def extract_table_as_dicts(self, soup, table_index=None):
        """
        Extract table data as dictionary format.

        Parameters
        ----------
        soup : BeautifulSoup
            BeautifulSoup object containing the HTML table
        table_index : int, optional
            Index of the table for unique key naming, by default None

        Returns
        -------
        dict
            Dictionary containing extracted table data with header-based keys
            or key-value pairs
        """
        rows = soup.find_all("tr")
        header_cells = rows[0].find_all("th") if rows else []
        if header_cells:
            headers = [cell.get_text(strip=True) for cell in header_cells]
            result = []
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) == len(headers):
                    row_dict = {
                        headers[i]: cells[i].get_text(strip=True)
                        for i in range(len(headers))
                    }
                    result.append(row_dict)
            key = f"table_{table_index}_rows" if table_index is not None else "rows"
            return {key: result}
        else:
            result = {}
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    if key and value:
                        result[key] = value
            key = (
                f"table_{table_index}_keyvals" if table_index is not None else "keyvals"
            )
            return {key: result}

    def extract_key_value_pairs_from_lines(self, text):
        """
        Extract key-value pairs from text lines (excluding HTML tables).

        Parameters
        ----------
        text : str
            Text content to extract key-value pairs from

        Returns
        -------
        dict
            Dictionary of key-value pairs extracted from non-table text lines
        """
        pairs = {}
        in_table = False
        for line in text.splitlines():
            # Detect start/end of HTML table
            if "<table>" in line:
                in_table = True
            if "</table>" in line:
                in_table = False
                continue
            if in_table:
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                if key and value:
                    pairs[key] = value
        return pairs

    def extract_comments(self):
        """
        Extract page header and footer from HTML comments.

        Returns
        -------
        dict
            Dictionary with 'PageHeader' and 'PageFooter' keys containing
            extracted values or empty strings if not found
        """
        header = re.search(r'', self.markdown_content)
        footer = re.search(r'', self.markdown_content)
        return {
            "PageHeader": header.group(1) if header else "",
            "PageFooter": footer.group(1) if footer else "",
        }

    def extract_section_list(self, section_names=None):
        """
        Returns a dict of section_name: [list of values] for each matching section header.

        Parameters
        ----------
        section_names : list, optional
            List of section header keywords to search for (case-insensitive, partial match),
            by default None

        Returns
        -------
        dict
            Dictionary mapping section names to lists of extracted values
        """
        if section_names is None:
            section_names = [
                "RELATED DOCUMENTS",
                "IMPACTED BUSINESS AREA",
                "IMPACTED BUSINESS AREAS",
            ]

        # Find all section headers (lines starting with ## or # and containing section name)
        lines = self.markdown_content.splitlines()
        results = {}
        current_section = None
        collecting = False
        section_values = []

        for line in lines:
            header_match = re.match(r"^(#{1,3})\s+(.+)", line)
            if header_match:
                header_text = header_match.group(2).strip().upper()
                matched_section = None
                for name in section_names:
                    if name == header_text:
                        matched_section = name
                        break
                if matched_section:
                    # Start collecting values for this section
                    if current_section and section_values:
                        results[current_section] = section_values
                    current_section = matched_section
                    collecting = True
                    section_values = []
                else:
                    collecting = False
            elif collecting:
                # Collect bullet points or lines until next header
                bullet_match = re.match(
                    r"^[\*\-\•]\s*(.+)", line
                )
                # bullet points
                if bullet_match:
                    section_values.append(bullet_match.group(1).strip())
                elif line.strip() and not line.strip().startswith("#"):
                    section_values.append(line.strip())

        # Add last collected section
        if current_section and section_values:
            results[current_section] = section_values

        return results

    def get_metadata(self):
        """
        Extract all metadata from markdown content including tables and sections.

        Returns
        -------
        dict
            Comprehensive metadata dictionary containing key-value pairs from tables,
            text lines, and section lists
        """
        metadata = {}
        # Extract key value pairs from all tables
        for soup in self.soup_tables:
            pairs = self.extract_key_value_pairs(soup)
            metadata.update(pairs)
        # Extract structured table data
        for i, soup in enumerate(self.soup_tables):
            pairs = self.extract_table_as_dicts(soup, table_index=i)
            metadata.update(pairs)
        # Extract all dates
        metadata.update(self.extract_key_value_pairs_from_lines(self.markdown_content))
        # Extract all sections
        metadata.update(self.extract_section_list())
        return metadata


class FindMetadataFields:
    """
    Search and retrieve specific metadata fields using pattern matching.

    Parameters
    ----------
    metadata : dict
        Metadata dictionary to search within

    Attributes
    ----------
    metadata : dict
        The metadata dictionary being searched
    """

    def __init__(self, metadata):
        self.metadata = metadata

    def find_by_pattern(self, pattern, metadata=None, exclude_patterns=None):
        """
        Generic function to search for metadata fields by regex pattern.

        Parameters
        ----------
        pattern : str
            Regex pattern to search for in keys
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata
        exclude_patterns : list, optional
            List of regex patterns to exclude

        Returns
        -------
        list
            List of matching values
        """
        if metadata is None:
            metadata = self.metadata
        if exclude_patterns is None:
            exclude_patterns = []

        results = []
        for k, v in metadata.items():
            if isinstance(v, dict):
                results.extend(self.find_by_pattern(pattern, v, exclude_patterns))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        results.extend(
                            self.find_by_pattern(pattern, item, exclude_patterns)
                        )
            else:
                # Check if key matches the pattern
                if re.search(pattern, k, re.IGNORECASE):
                    # Check if key should be excluded
                    excluded = any(
                        re.search(exclude_pattern, k, re.IGNORECASE)
                        for exclude_pattern in exclude_patterns
                    )
                    if not excluded:
                        results.append(v)
        return results

    def find_number_or_identifier(self, metadata=None):
        """
        Find number or identifier fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching number or identifier values
        """
        return self.find_by_pattern(
            pattern=r"\b(number|identifier)\b",
            metadata=metadata,
            exclude_patterns=[r"\bpage number\b"],
        )

    def find_title(self, metadata=None):
        """
        Find title fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching title values
        """
        return self.find_by_pattern(r"\b(title)\b", metadata)

    def find_department(self, metadata=None):
        """
        Find department fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching department values
        """
        return self.find_by_pattern(r"\b(department)\b", metadata)

    def find_effective_date(self, metadata=None):
        """
        Find effective date fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching effective date values
        """
        return self.find_by_pattern(r"\b(effective date)\b", metadata)

    def find_approval_date(self, metadata=None):
        """
        Find approval date fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching approval date values
        """
        return self.find_by_pattern(r"\b(approval date)\b", metadata)

    def find_business_owner(self, metadata=None):
        """
        Find business owner fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching business owner values
        """
        return self.find_by_pattern(r"\b(business owner)\b", metadata)

    def find_executive_owner(self, metadata=None):
        """
        Find executive owner fields in metadata.

        Parameters
        ----------
        metadata : dict, optional
            Metadata to search in. Defaults to self.metadata

        Returns
        -------
        list
            List of matching executive owner values
        """
        return self.find_by_pattern(r"\b(executive owner)\b", metadata)


class SharePointDocsChunker(BaseDocumentChunker):
    def __init__(
        self,
        data_config: Union[str, Dict[str, Any]]
    ) -> None:
        """
        Initialize the SharePointDocsChunker with configuration and Azure Document Intelligence client.

        Parameters
        ----------
        data_config : Union[str, Dict[str, Any]]
            Configuration data either as a file path string or dictionary containing
            all necessary configuration parameters for document processing and Azure services.
        """
        if isinstance(data_config, str):
            self.data_config = load_config(data_config)
        else:
            self.data_config = data_config
        super().__init__(
            data_config=self.data_config
        )
        self.logger = create_logger(
            logger_name=self.data_config.logging.logger_name,
            level=self.data_config.logging.logging_level,
            log_file_path=self.data_config.logging.log_file_path,
        )

        self.logger.info("Initializing SharePointDocsChunker")

        load_dotenv(".env")
        AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID_PROD")
        AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET_PROD")
        AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID_PROD")

        if not all([AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID]):
            self.logger.warning("Missing Azure credentials in environment variables")

        self.credential = ClientSecretCredential(
            tenant_id=AZURE_TENANT_ID,
            client_id=AZURE_CLIENT_ID,
            client_secret=AZURE_CLIENT_SECRET,
        )

        self.texts_to_replace = self.data_config.delia.policy_documents.texts_to_replace
        self.headers_to_split_on = (
            self.data_config.delia.policy_documents.markdown_headers_to_split_on
        )
        self.separators = (
            self.data_config.delia.policy_documents.recursive_split_separators
        )
        self.markdown_symbols_to_skip = (
            self.data_config.delia.policy_documents.markdown_symbols_to_skip
        )
        self.max_tokens = (
            self.data_config.delia.policy_documents.recursive_split_max_tokens
        )
        self.overlap_tokens = (
            self.data_config.delia.policy_documents.recursive_split_overlap_tokens
        )
        self.min_tokens_to_merge = (
            self.data_config.delia.policy_documents.min_tokens_to_merge
        )

        self.endpoint = self.data_config.delia.azure_document_intelligence.endpoint
        self.https_proxy = (
            self.data_config.delia.azure_document_intelligence.HTTPS_PROXY
        )
        self.http_proxy = self.data_config.delia.azure_document_intelligence.HTTP_PROXY
        self.model_id = self.data_config.delia.azure_document_intelligence.model_id
        self.output_content_format = (
            self.data_config.delia.azure_document_intelligence.output_content_format
        )
        self.connection_verify = (
            self.data_config.delia.azure_document_intelligence.connection_verify
        )

        self.client = self._document_intelligence_client()

        self.logger.info("SharePointDocsChunker initialization completed successfully")

    def _document_intelligence_client(self) -> DocumentIntelligenceClient:
        """
        Initializes and returns an Azure DocumentIntelligenceClient with configured proxies.

        Returns
        -------
        DocumentIntelligenceClient
            The initialized client for document intelligence.
        """
        client = DocumentIntelligenceClient(
            endpoint=self.endpoint,
            credential=self.credential,
            proxies={"http": self.http_proxy, "https": self.https_proxy},
            connection_verify=self.connection_verify,
        )
        return client

    def _document_analysis(
        self, file_path: Optional[str] = None, file: Optional[Any] = None
    ) -> Any:
        """
        Analyze a document using Azure Document Intelligence.

        Parameters
        ----------
        file_path : Optional[str], optional
            Path to the document file to analyze, by default None
        file : Optional[Any], optional
            File-like object containing the document content, by default None

        Returns
        -------
        Any
            Document analysis result from Azure Document Intelligence

        Raises
        ------
        ValueError
            If neither file_path nor file is provided
        Exception
            If document analysis fails
        """
        if file is not None:
            file_obj = file
            self.logger.debug("Analyzing document from file object")
        elif file_path is not None:
            file_obj = open(file_path, "rb")
            self.logger.debug(f"Analyzing document from file path: {file_path}")
        else:
            error_msg = "Either file_path or file must be provided."
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        try:
            self.logger.info(f"Starting document analysis with model: {self.model_id}")
            poller = self.client.begin_analyze_document(
                model_id=self.model_id,
                body=file_obj,
                output_content_format=self.output_content_format,
                # connection_verify=self.connection_verify,
            )
            result = poller.result()
            self.logger.info("Document analysis completed successfully")
            return result
        except Exception as e:
            error_msg = f"Error analyzing document: {e}"
            self.logger.error(f"{error_msg}\n{traceback.format_exc()}")
            raise
        finally:
            if file is None and file_obj is not None:
                file_obj.close()
                self.logger.debug("Closed document file")

    def get_document_intelligence_results(
        self, list_of_file_contents: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Process a list of file contents through Azure Document Intelligence.

        Parameters
        ----------
        list_of_file_contents : List[Dict[str, Any]]
            List of dictionaries containing file details with keys:
            'content', 'name', 'id', 'lastModifiedDateTime', 'url', 'version'

        Returns
        -------
        List[Dict[str, Any]]
            List of document intelligence results with added metadata
        """
        self.logger.info(
            f"Processing {len(list_of_file_contents)} files through Document Intelligence"
        )
        document_intelligence_results = []

        for i, file_details in enumerate(list_of_file_contents):
            try:
                self.logger.debug(
                    f"Processing file {i+1}/{len(list_of_file_contents)}: {file_details.get('name', 'Unknown')}"
                )
                result = self._document_analysis(file=file_details["content"])

                result.update({k: v for k, v in file_details.items() if k != "content"})

                document_intelligence_results.append(result)
                self.logger.debug(
                    f"Successfully processed file: {file_details.get('name', 'Unknown')}"
                )

            except Exception as e:
                self.logger.error(
                    f"Failed to process file {file_details.get('name', 'Unknown')}: {e}"
                )
                continue

        self.logger.info(
            f"Successfully processed {len(document_intelligence_results)} files"
        )
        return document_intelligence_results

    def add_metadata_to_results(
        self, results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract and add metadata fields to document intelligence results.

        Parameters
        ----------
        results : List[Dict[str, Any]]
            List of document intelligence results to enhance with metadata

        Returns
        -------
        List[Dict[str, Any]]
            List of results with added metadata fields including title, sourceId,
            creation date, approval date, business owner, executive owner, and department
        """
        self.logger.info(f"Adding metadata to {len(results)} results")

        for i, result in enumerate(results):
            try:
                self.logger.debug(
                    f"Extracting metadata for result {i+1}/{len(results)}"
                )
                extractor = MarkdownMetadataExtractor(result["content"])

                # Update the content with the cleaned markdown content
                result["content"] = extractor.markdown_content

                metadata = extractor.get_metadata()
                find_metadata = FindMetadataFields(metadata)

                # Add other metadata fields to the result
                title = find_metadata.find_title()
                if title:
                    result["title"] = title[0]
                    self.logger.debug(f"Found title: {title[0]}")
                else:
                    result["title"] = result["name"]
                    self.logger.debug(f"Using filename as title: {result['name']}")

                policy_number = find_metadata.find_number_or_identifier()
                if policy_number:
                    result["sourceId"] = policy_number[0]
                    self.logger.debug(f"Found policy number: {policy_number[0]}")
                else:
                    result["sourceId"] = ""

                creation_date = find_metadata.find_effective_date()
                if creation_date:
                    result["documentCreationDate"] = creation_date[0]
                    self.logger.debug(f"Found creation date: {creation_date[0]}")
                else:
                    result["documentCreationDate"] = ""

                approval_date = find_metadata.find_approval_date()
                if approval_date:
                    result["documentLastUpdateDate"] = approval_date[0]
                    self.logger.debug(f"Found approval date: {approval_date[0]}")
                else:
                    result["documentLastUpdateDate"] = ""

                business_owner = find_metadata.find_business_owner()
                if business_owner:
                    result["businessOwner"] = business_owner[0]
                    self.logger.debug(f"Found business owner: {business_owner[0]}")
                else:
                    result["businessOwner"] = ""

                executive_owner = find_metadata.find_executive_owner()
                if executive_owner:
                    result["executiveOwner"] = executive_owner[0]
                    self.logger.debug(f"Found executive owner: {executive_owner[0]}")
                else:
                    result["executiveOwner"] = ""

                department = find_metadata.find_department()
                if department:
                    result["department"] = department[0]
                    self.logger.debug(f"Found department: {department[0]}")
                else:
                    result["department"] = ""

            except Exception as e:
                self.logger.error(f"Error extracting metadata for result {i+1}: {e}")
                # Set default values on error
                if "title" not in result:
                    result["title"] = result.get("name", "")
                for field in [
                    "sourceId",
                    "documentCreationDate",
                    "documentLastUpdateDate",
                    "businessOwner",
                    "executiveOwner",
                    "department",
                ]:
                    if field not in result:
                        result[field] = ""

        self.logger.info("Metadata extraction completed for all results")
        return results

    def _html_table_to_json(self, html: str) -> Optional[List[Dict[str, str]]]:
        """
        Convert an HTML table to JSON format.

        Parameters
        ----------
        html : str
            HTML string containing a table element

        Returns
        -------
        Optional[List[Dict[str, str]]]
            List of dictionaries representing table rows, or None if no valid table found
        """
        self.logger.debug("Converting HTML table to JSON format")
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            self.logger.debug("No table element found in HTML")
            return None

        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            self.logger.debug("No table headers found")
            return None

        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) == len(headers):
                row = {
                    headers[i]: cells[i].get_text(strip=True)
                    for i in range(len(headers))
                }
                rows.append(row)

        self.logger.debug(
            f"Converted table with {len(rows)} rows and {len(headers)} columns"
        )
        return rows if rows else None

    def replace_html_tables_with_json(self, markdown_content: str) -> str:
        """
        Replace HTML tables in markdown content with JSON code blocks.

        Parameters
        ----------
        markdown_content : str
            Markdown content containing HTML tables

        Returns
        -------
        str
            Markdown content with HTML tables replaced by JSON code blocks
        """
        self.logger.debug("Replacing HTML tables with JSON format")

        def replacer(match):
            html = match.group(0)
            json_data = self._html_table_to_json(html)
            if json_data is not None:
                return "\n```table\n" + json.dumps(json_data, indent=2) + "\n```\n"
            else:
                # Wrap in list-of-dicts format expected by postprocess_html_to_markdown
                converted = self.postprocess_html_to_markdown([{"content": html}])
                return converted[0]["content"]

        result = re.sub(
            r"<table>.*?</table>", replacer, markdown_content, flags=re.DOTALL
        )

        self.logger.debug("HTML table replacement completed")
        return result

    def remove_figures(self, markdown_content: str) -> str:
        """
        Remove all <figure>...</figure> blocks from the markdown content.

        Parameters
        ----------
        markdown_content : str
            Markdown content containing figure elements

        Returns
        -------
        str
            Markdown content with figure elements removed
        """
        self.logger.debug("Removing figure elements from markdown content")
        result = re.sub(
            r"<figure>.*?</figure>", "", markdown_content, flags=re.DOTALL
        )
        self.logger.debug("Figure removal completed")
        return result

    def process_documents(
        self, docs: List[Dict[str, Any]], add_headers: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Process documents through the complete chunking pipeline.

        This method performs the following steps:
        1. Replace HTML tables with JSON format
        2. Remove figure elements
        3. Split documents by markdown headers
        4. Add header hierarchy metadata
        5. Add token counts
        6. Recursively split large documents
        7. Optionally add headers to chunks
        8. Merge small chunks
        9. Add unique IDs

        Parameters
        ----------
        docs : List[Dict[str, Any]]
            List of document dictionaries to process
        add_headers : bool, optional
            Whether to add headers to chunks, by default True

        Returns
        -------
        List[Dict[str, Any]]
            List of processed document chunks with metadata and IDs
        """
        self.logger.info(
            f"Starting document processing pipeline for {len(docs)} documents"
        )

        docs_copy = deepcopy(docs)
        # Replace HTML tables with JSON
        self.logger.info("Converting HTML tables to JSON format")