"""Setup script to store Lakebase connection URL in Databricks secrets.

Run this once to configure your database credentials securely.
Never commit the connection URL to version control.

Usage:
    python setup_secrets.py
"""

import base64
import getpass

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace

w = WorkspaceClient()

# Secret scope and key names (must match lakebase.py)
SCOPE = "weather-db"
KEY = "lakebase-url"


def setup_secrets():
    """Create the secret scope and store the Lakebase URL."""
    
    print("Weather RAG App - Database Setup")
    print("=" * 50)
    print()
    
    # Create the secret scope
    try:
        w.secrets.create_scope(scope=SCOPE)
        print(f"✓ Created secret scope: {SCOPE}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"✓ Secret scope already exists: {SCOPE}")
        else:
            print(f"✗ Failed to create scope: {e}")
            return
    
    # Prompt for the Lakebase connection URL securely
    print()
    print("Enter your Lakebase Postgres connection URL:")
    print("Format: postgresql://user:password@host:port/database?sslmode=require")
    print()
    lakebase_url = getpass.getpass("Lakebase URL: ")
    
    if not lakebase_url or not lakebase_url.startswith("postgresql://"):
        print("✗ Invalid connection URL. Must start with 'postgresql://'")
        return
    
    # Base64-encode the connection URL
    encoded_url = base64.b64encode(lakebase_url.encode("utf-8")).decode("utf-8")
    
    # Store the secret
    try:
        w.secrets.put_secret(
            scope=SCOPE,
            key=KEY,
            string_value=encoded_url
        )
        print()
        print(f"✓ Stored secret: {SCOPE}/{KEY}")
        
        # Set read permissions for all users (optional - adjust as needed)
        try:
            w.secrets.put_acl(
                scope=SCOPE,
                principal="users",
                permission=workspace.AclPermission.READ,
            )
            print(f"✓ Set read permissions for users")
        except Exception as e:
            print(f"Warning: Could not set ACL permissions: {e}")
        
        print()
        print("=" * 50)
        print("✓ Setup complete! Your app can now connect to Lakebase.")
        print("=" * 50)
    except Exception as e:
        print(f"✗ Failed to store secret: {e}")


if __name__ == "__main__":
    setup_secrets()
