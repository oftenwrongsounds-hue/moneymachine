"""
Gumroad publisher — publishes digital products via Gumroad API.
Handles product creation, file upload placeholder, and webhook setup.
"""
import os
import sys
import logging
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests

logger = logging.getLogger(__name__)

GUMROAD_API = "https://api.gumroad.com/v2"


def _token() -> str:
    token = os.environ.get("GUMROAD_ACCESS_TOKEN")
    if not token:
        raise ValueError("GUMROAD_ACCESS_TOKEN environment variable not set")
    return token


def publish(
    title: str,
    description: str,
    price: int,  # in cents
    tags: list = None,
    content: str = "",
    published: bool = True,
) -> dict:
    """
    Create a product on Gumroad.

    Args:
        title: Product title
        description: Product description (HTML or plain text)
        price: Price in cents (0 = free)
        tags: List of tag strings
        content: The product content/template text
        published: Whether to publish immediately

    Returns:
        Gumroad API response dict
    """
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}

    # Create a temp file for the product content
    product_file = None
    if content:
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            )
            tmp.write(content)
            tmp.close()
            product_file = tmp.name
        except Exception as e:
            logger.warning(f"Could not create temp file for product content: {e}")

    data = {
        "name": title,
        "description": description,
        "price": price,
        "published": "true" if published else "false",
    }
    if tags:
        data["tags"] = ",".join(tags)

    try:
        response = requests.post(
            f"{GUMROAD_API}/products",
            headers=headers,
            data=data,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("success"):
            raise RuntimeError(f"Gumroad API returned failure: {result}")

        product_id = result.get("product", {}).get("id")
        logger.info(f"Created Gumroad product: {title} (id: {product_id})")

        # Upload content file if we have one
        if product_file and product_id:
            try:
                _upload_product_file(product_id, product_file, token)
            except Exception as e:
                logger.warning(f"Could not upload product file: {e}")
            finally:
                import os as _os
                try:
                    _os.unlink(product_file)
                except Exception:
                    pass

        return result

    except requests.exceptions.RequestException as e:
        logger.error(f"Gumroad API error: {e}")
        raise


def _upload_product_file(product_id: str, file_path: str, token: str) -> dict:
    """Upload a file to a Gumroad product."""
    headers = {"Authorization": f"Bearer {token}"}
    with open(file_path, "rb") as f:
        response = requests.put(
            f"{GUMROAD_API}/products/{product_id}/product_files",
            headers=headers,
            files={"file": (Path(file_path).name, f, "application/octet-stream")},
            timeout=60,
        )
    response.raise_for_status()
    return response.json()


def update_product(product_id: str, fields: dict) -> dict:
    """Update an existing Gumroad product."""
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.put(
            f"{GUMROAD_API}/products/{product_id}",
            headers=headers,
            data=fields,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update Gumroad product {product_id}: {e}")
        raise


def list_products() -> list:
    """List all Gumroad products."""
    token = _token()
    try:
        response = requests.get(
            f"{GUMROAD_API}/products",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("products", [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to list Gumroad products: {e}")
        return []


def test_connection() -> bool:
    """Test Gumroad API connectivity."""
    try:
        products = list_products()
        return True
    except Exception as e:
        logger.error(f"Gumroad connection test failed: {e}")
        return False
