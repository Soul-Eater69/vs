import os
import dotenv
import requests
import platform
import traceback
from typing import Optional, Union
from delia.utils.utils import create_logger, load_config

def ensure_directory_exists(file_path):
    """
    Ensure that the directory for a given file path exists, creating it if necessary.

    Parameters
    ----------
    file_path : str
        The full file path for which to ensure the directory exists

    Returns
    -------
    None
    """
    directory = os.path.dirname(file_path)
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def get_long_path(path):
    """
    Convert a path to handle long file paths on Windows systems.

    On Windows, applies the \\\\?\\ prefix to enable long path support (> 260 characters).
    On Unix based systems, returns the absolute path without modification.

    Parameters
    ----------
    path : str
        The file path to convert

    Returns
    -------
    str
        The converted path with long path support on Windows, or absolute path on Unix systems
    """
    # Check if the operating system is Windows
    if platform.system() == "Windows":
        # Apply the \\?\ prefix correctly to handle long paths on Windows
        return "\\\\?\\" + os.path.abspath(path).strip()
    else:
        # Return the normal path for Unix-based systems
        return os.path.abspath(path)

class SharePointClient:
    def __init__(
        self,
        data_config: Union[str, object],
    ) -> None:
        """
        Initialize the SharePointClient.

        Parameters
        ----------
        data_config : Union[str, object]
            Path to the configuration file or a configuration object.
        tenant_id : str
            Azure AD tenant ID.
        client_id : str
            Azure AD application client ID.
        client_secret : str
            Azure AD application client secret.

        Returns
        -------
        None
        """
        if isinstance(data_config, str):
            self.data_config = load_config(data_config)
        else:
            self.data_config = data_config

        dotenv.load_dotenv(".env")
        self.tenant_id = os.getenv("AZURE_TENANT_ID")
        self.client_id = os.getenv("GRAPH_CLIENT_ID")
        self.client_secret = os.getenv("GRAPH_CLIENT_SECRET")

        self.logger = create_logger(
            logger_name=self.data_config.logging.logger_name,
            level=self.data_config.logging.logging_level,
            log_file_path=self.data_config.logging.log_file_path,
        )

        self.resource_url = self.data_config.delia.sharepoint.resource_url
        self.base_url = self.data_config.delia.sharepoint.oauth_endpoint.format(
            tenant_id=self.tenant_id
        )
        self.access_token = self.get_access_token()

    def get_access_token(self) -> Optional[str]:
        """
        Retrieve an access token from Microsoft's OAuth2 endpoint.

        The access token is used to authenticate and authorize the application for
        accessing Microsoft Graph API resources.

        Returns
        -------
        Optional[str]
            The access token as a string if successful, otherwise None.
        """
        body = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.resource_url + ".default",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            response = requests.post(self.base_url, headers=headers, data=body)
            response.raise_for_status()
            return response.json().get("access_token")
        except Exception as e:
            self.logger.error(
                f"Error getting access token: {e}\n{traceback.format_exc()}"
            )
            return None

    def get_site_details(self, site_url: str) -> Optional[dict]:
        """
        Retrieve the details of a SharePoint site using the Microsoft Graph API.

        Parameters
        ----------
        site_url : str
            The URL of the SharePoint site.

        Returns
        -------
        Optional[dict]
            The details of the SharePoint site as a dictionary if successful, otherwise None.
        """
        full_url = f"{self.resource_url}v1.0/sites/{site_url}"
        try:
            response = requests.get(
                full_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(
                f"Error getting site details for '{site_url}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_drive_details(self, site_id: str) -> Optional[list]:
        """
        Retrieve the details of all drives associated with a specified SharePoint site.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.

        Returns
        -------
        Optional[list]
            A list of dictionaries, each representing a drive on the SharePoint site,
            or None if an error occurs.
        """
        drives_url = f"{self.resource_url}v1.0/sites/{site_id}/drives"
        try:
            response = requests.get(
                drives_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            response.raise_for_status()
            return response.json().get("value", [])
        except Exception as e:
            self.logger.error(
                f"Error getting drive details for site '{site_id}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_folder_details(
        self, site_id: str, drive_id: str, folder_path: str
    ) -> Optional[dict]:
        """
        Retrieve the details of a specified subfolder in a SharePoint drive.

        Parameters
        ----------
        site_id : str
            The ID of the site where the subfolder is located.
        drive_id : str
            The ID of the drive where the subfolder is located.
        folder_path : str
            The path of the subfolder whose details are to be retrieved, separated by '/'.

        Returns
        -------
        Optional[dict]
            The details of the specified subfolder as a dictionary if found, otherwise None.
        """
        folders = folder_path.split("/")

        # Start with the root folder
        current_folder_id = "root"

        for folder_name in folders:
            # Build the URL to access the contents of the current folder
            folder_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{current_folder_id}/children"
            response = requests.get(
                folder_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            
            items_data = response.json()
            # Loop through the items and find the folder
            for item in items_data.get("value", []):
                if "folder" in item and item["name"] == folder_name:
                    # Update the current folder ID and break the loop
                    current_folder_id = item["id"]
                    break
            else:
                return None

        # Return the details of the final folder details in the path
        folder_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{current_folder_id}"
        response = requests.get(
            folder_url, headers={"Authorization": f"Bearer {self.access_token}"}
        )
        return response.json()

    def get_folder_content(
        self,
        site_id: str,
        drive_id: str,
        folder_id: str,
    ) -> Optional[list]:
        """
        Retrieve the contents of a specific folder in a drive on a SharePoint site.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        folder_id : str
            The ID of the folder whose contents are to be retrieved.

        Returns
        -------
        Optional[list]
            A list of dictionaries representing the items in the folder if successful, otherwise None.
        """
        folder_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        try:
            response = requests.get(
                folder_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            response.raise_for_status()
            items_data = response.json()
            return items_data.get("value", [])
        except Exception as e:
            self.logger.error(
                f"Error getting folder content for site '{site_id}', drive '{drive_id}', folder '{folder_id}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_file_details(
        self, site_id: str, drive_id: str, file_id: str
    ) -> Optional[dict]:
        """
        Retrieve the details of a specified file in a SharePoint drive.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        file_id : str
            The ID of the file whose details are to be retrieved.

        Returns
        -------
        Optional[dict]
            The details of the specified file as a dictionary if successful, otherwise None.
        """
        file_url = (
            f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{file_id}"
        )
        try:
            response = requests.get(
                file_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(
                f"Error getting file details for site '{site_id}', drive '{drive_id}', file '{file_id}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_file_content(
        self, site_id: str, drive_id: str, file_id: str
    ) -> Optional[bytes]:
        """
        Retrieve the content of a specified file in a SharePoint drive.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        file_id : str
            The ID of the file whose content is to be retrieved.

        Returns
        -------
        Optional[bytes]
            The content of the specified file as bytes if successful, otherwise None.
        """
        file_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{file_id}/content"
        try:
            response = requests.get(
                file_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                allow_redirects=True,
                verify=False,
            )

            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(
                f"Error getting file content for site '{site_id}', drive '{drive_id}', file '{file_id}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_file_content_by_url(self, download_url: str) -> Optional[bytes]:
        """
        Retrieve the content of a file from a given download URL.

        Parameters
        ----------
        download_url : str
            The URL from which to download the file content.

        Returns
        -------
        Optional[bytes]
            The content of the file as bytes if successful, otherwise None.
        """
        try:
            response = requests.get(
                download_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                allow_redirects=True,
                verify=False,
            )

            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(
                f"Error getting file content by URL '{download_url}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_file_versions(
        self, site_id: str, drive_id: str, file_id: str
    ) -> Optional[list]:
        """
        Retrieve the version history of a specified file in a SharePoint drive.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        file_id : str
            The ID of the file whose versions are to be retrieved.

        Returns
        -------
        Optional[list]
            A list of dictionaries representing the file versions if successful, otherwise None.
        """
        item_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{file_id}/versions"
        try:
            response = requests.get(
                item_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            response.raise_for_status()
            return response.json().get("value", [])
        except Exception as e:
            self.logger.error(
                f"Error getting file versions for site '{site_id}', drive '{drive_id}', file '{file_id}': {e}\n{traceback.format_exc()}"
            )
            return None

    def get_file_content_by_version(
        self, site_id: str, drive_id: str, file_id: str, version_id: str
    ) -> Optional[bytes]:
        """
        Retrieve the content of a specific version of a file in a SharePoint drive.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        file_id : str
            The ID of the file whose version content is to be retrieved.
        version_id : str
            The ID of the file version to retrieve.

        Returns
        -------
        Optional[bytes]
            The content of the specified file version as bytes if successful, otherwise None.
        """
        item_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{file_id}/versions/{version_id}/content"
        try:
            response = requests.get(
                item_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                allow_redirects=True,
                verify=False,
            )
            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(
                f"Error getting file content for version '{version_id}' of file '{file_id}' on site '{site_id}', drive '{drive_id}': {e}\n{traceback.format_exc()}"
            )
            return None

    def _download_file(
        self, download_url: str, local_path: str, file_name: str
    ) -> None:
        """
        Download a file from a specified URL and save it to a local path.

        Parameters
        ----------
        download_url : str
            The URL of the file to be downloaded.
        local_path : str
            The local directory where the file will be saved.
        file_name : str
            The name of the file to be saved.

        Returns
        -------
        None
            The function prints a success message if the file is downloaded and saved successfully,
            or an error message if the download fails.
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(download_url, headers=headers)
        if response.status_code == 200:
            full_path = os.path.join(local_path, file_name)
            full_path = get_long_path(
                full_path
            )  # Apply the long path fix conditionally based on the OS
            ensure_directory_exists(full_path)
            with open(full_path, "wb") as file:
                file.write(response.content)
            # print(f"File downloaded: {full_path}")
        else:
            print(
                f"Failed to download {file_name}: {response.status_code} - {response.reason}"
            )

    def download_folder_contents(
        self,
        site_id: str,
        drive_id: str,
        folder_id: str,
        local_folder_path: str,
        level: int = 0,
    ) -> None:
        """
        Recursively download all contents from a specified folder on a SharePoint site.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        folder_id : str
            The ID of the folder whose contents are to be downloaded.
        local_folder_path : str
            The local path where the downloaded files will be saved.
        level : int, optional
            The current level of recursion (folder depth). Defaults to 0 for the root folder.

        Returns
        -------
        None
            The function saves the downloaded files to the specified local path.
        """
        folder_contents_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        contents_headers = {"Authorization": f"Bearer {self.access_token}"}
        contents_response = requests.get(folder_contents_url, headers=contents_headers)
        folder_contents = contents_response.json()

        if "value" in folder_contents:
            for item in folder_contents["value"]:
                if "folder" in item:
                    self.logger.info(
                        f"Accessing folder '{item.get('name')}' (id: {item.get('id')}) at level {level}"
                    )
                    new_path = os.path.join(local_folder_path, item["name"])
                    if not os.path.exists(new_path):
                        os.makedirs(new_path, exist_ok=True)
                    self.logger.debug(f"Created local directory: {new_path}")
                    self.download_folder_contents(
                        site_id, drive_id, item["id"], new_path, level + 1
                    )
                elif "file" in item:
                    file_name = item["name"]
                    self.logger.info(
                        f"Accessing file '{file_name}' (id: {item.get('id')}) in '{local_folder_path}'"
                    )
                    self.download_file_contents(
                        site_id=site_id,
                        drive_id=drive_id,
                        file_id=item["id"],
                        local_save_path=local_folder_path,
                    )

    def download_file_contents(
        self,
        site_id: str,
        drive_id: str,
        file_id: str,
        local_save_path: str,
    ) -> bool:
        """
        Download the contents of a specified file from a SharePoint site and save it to a local path.

        Parameters
        ----------
        site_id : str
            The ID of the SharePoint site.
        drive_id : str
            The ID of the drive on the SharePoint site.
        file_id : str
            The ID of the file to be downloaded.
        local_save_path : str
            The local path where the downloaded file will be saved.

        Returns
        -------
        bool
            True if the file was successfully downloaded and saved, False otherwise.
        """
        try:
            file_url = f"{self.resource_url}v1.0/sites/{site_id}/drives/{drive_id}/items/{file_id}"
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(file_url, headers=headers)
            file_data = response.json()

            download_url = file_data["@microsoft.graph.downloadUrl"]
            file_name = file_data["name"]
            sharepoint_file_path = file_data["parentReference"]["path"]
            index = sharepoint_file_path.find(":/")

            if index != -1:
                extracted_path = sharepoint_file_path[index + 2 :]
                local_save_path = os.path.join(local_save_path, extracted_path)
                os.makedirs(local_save_path, exist_ok=True)
            else:
                extracted_path = ""
            self._download_file(download_url, local_save_path, file_name)
            return True

        except requests.exceptions.RequestException as e:
            print(f"Error downloading file: {file_id} err: {e}")
            return False