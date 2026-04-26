"""SharePoint client via Microsoft Graph API (client credentials)."""
from __future__ import annotations

import base64
import logging
import os
import platform
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0/"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _get_long_path(path: str) -> str:
    if platform.system() == "Windows":
        return "\\\\?\\" + os.path.abspath(path).strip()
    return os.path.abspath(path)


def _ensure_dir(file_path: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)


class SharePointClient:
    def __init__(self) -> None:
        self.tenant_id = os.environ.get("AZURE_TENANT_ID_SP", "")
        self.client_id = os.environ.get("GRAPH_CLIENT_ID", "")
        self.client_secret = os.environ.get("GRAPH_CLIENT_SECRET", "")
        self.access_token: Optional[str] = self.get_access_token()

    def get_access_token(self) -> Optional[str]:
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            logger.error("AZURE_TENANT_ID_SP / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET not set")
            return None
        try:
            resp = requests.post(
                _TOKEN_URL.format(tenant_id=self.tenant_id),
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("access_token")
        except Exception as exc:
            logger.error("Error getting access token: %s", exc)
            return None

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    # ------------------------------------------------------------------
    # Sharing-URL resolution (browser URLs → Graph item metadata)
    # ------------------------------------------------------------------

    @staticmethod
    def encode_sharing_url(url: str) -> str:
        """Encode a SharePoint browser URL into a Graph sharing token."""
        encoded = base64.urlsafe_b64encode(url.encode()).rstrip(b"=").decode()
        return f"u!{encoded}"

    def resolve_browser_url(self, url: str) -> Optional[dict]:
        """
        Resolve any SharePoint browser URL to its Graph driveItem metadata.
        Returns the item dict on success, None on failure.
        Works with any myfyi.sharepoint.com/... URL without needing site/drive IDs.
        """
        token = self.encode_sharing_url(url)
        try:
            resp = requests.get(
                f"{_GRAPH_BASE}shares/{token}/driveItem",
                headers=self._auth_headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.debug("resolve_browser_url %s → %d %s", url[:80], resp.status_code, resp.text[:200])
            return None
        except Exception as exc:
            logger.warning("resolve_browser_url failed for %s: %s", url[:80], exc)
            return None

    # ------------------------------------------------------------------
    # Site / drive / folder / file navigation
    # ------------------------------------------------------------------

    def get_site_details(self, site_url: str) -> Optional[dict]:
        try:
            resp = requests.get(f"{_GRAPH_BASE}sites/{site_url}", headers=self._auth_headers(), timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("get_site_details %s: %s", site_url, exc)
            return None

    def get_drive_details(self, site_id: str) -> Optional[list]:
        try:
            resp = requests.get(f"{_GRAPH_BASE}sites/{site_id}/drives", headers=self._auth_headers(), timeout=15)
            resp.raise_for_status()
            return resp.json().get("value", [])
        except Exception as exc:
            logger.error("get_drive_details %s: %s", site_id, exc)
            return None

    def get_folder_details(self, site_id: str, drive_id: str, folder_path: str) -> Optional[dict]:
        current_folder_id = "root"
        for folder_name in folder_path.split("/"):
            url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{current_folder_id}/children"
            resp = requests.get(url, headers=self._auth_headers(), timeout=15)
            for item in resp.json().get("value", []):
                if "folder" in item and item["name"] == folder_name:
                    current_folder_id = item["id"]
                    break
            else:
                return None
        url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{current_folder_id}"
        return requests.get(url, headers=self._auth_headers(), timeout=15).json()

    def get_folder_content(self, site_id: str, drive_id: str, folder_id: str) -> Optional[list]:
        try:
            url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
            resp = requests.get(url, headers=self._auth_headers(), timeout=15)
            resp.raise_for_status()
            return resp.json().get("value", [])
        except Exception as exc:
            logger.error("get_folder_content %s/%s/%s: %s", site_id, drive_id, folder_id, exc)
            return None

    def get_file_details(self, site_id: str, drive_id: str, file_id: str) -> Optional[dict]:
        try:
            url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{file_id}"
            resp = requests.get(url, headers=self._auth_headers(), timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("get_file_details %s: %s", file_id, exc)
            return None

    def get_file_content(self, site_id: str, drive_id: str, file_id: str) -> Optional[bytes]:
        try:
            url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{file_id}/content"
            resp = requests.get(url, headers=self._auth_headers(), allow_redirects=True, verify=False, timeout=15)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.error("get_file_content %s: %s", file_id, exc)
            return None

    def get_file_content_by_url(self, download_url: str) -> Optional[bytes]:
        try:
            resp = requests.get(download_url, headers=self._auth_headers(), allow_redirects=True, verify=False, timeout=15)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.error("get_file_content_by_url %s: %s", download_url[:80], exc)
            return None

    def get_file_versions(self, site_id: str, drive_id: str, file_id: str) -> Optional[list]:
        try:
            url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{file_id}/versions"
            resp = requests.get(url, headers=self._auth_headers(), timeout=15)
            resp.raise_for_status()
            return resp.json().get("value", [])
        except Exception as exc:
            logger.error("get_file_versions %s: %s", file_id, exc)
            return None

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    def download_file_contents(self, site_id: str, drive_id: str, file_id: str, local_save_path: str) -> bool:
        try:
            url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{file_id}"
            file_data = requests.get(url, headers=self._auth_headers(), timeout=15).json()
            download_url = file_data["@microsoft.graph.downloadUrl"]
            file_name = file_data["name"]
            sp_path = file_data["parentReference"]["path"]
            idx = sp_path.find(":/")
            if idx != -1:
                local_save_path = os.path.join(local_save_path, sp_path[idx + 2:])
                os.makedirs(local_save_path, exist_ok=True)
            self._download_file(download_url, local_save_path, file_name)
            return True
        except Exception as exc:
            logger.error("download_file_contents %s: %s", file_id, exc)
            return False

    def download_folder_contents(self, site_id: str, drive_id: str, folder_id: str, local_folder_path: str, level: int = 0) -> None:
        url = f"{_GRAPH_BASE}sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        resp = requests.get(url, headers=self._auth_headers(), timeout=15)
        for item in resp.json().get("value", []):
            if "folder" in item:
                new_path = os.path.join(local_folder_path, item["name"])
                os.makedirs(new_path, exist_ok=True)
                self.download_folder_contents(site_id, drive_id, item["id"], new_path, level + 1)
            elif "file" in item:
                self.download_file_contents(site_id, drive_id, item["id"], local_folder_path)

    def _download_file(self, download_url: str, local_path: str, file_name: str) -> None:
        resp = requests.get(download_url, headers=self._auth_headers(), timeout=30)
        if resp.status_code == 200:
            full_path = _get_long_path(os.path.join(local_path, file_name))
            _ensure_dir(full_path)
            with open(full_path, "wb") as fh:
                fh.write(resp.content)
        else:
            logger.warning("Failed to download %s: %d %s", file_name, resp.status_code, resp.reason)
