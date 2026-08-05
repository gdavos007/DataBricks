# ⚡ ThunderHawk Ticketing System

A full-stack support ticketing application built with Streamlit and Databricks Lakebase (Postgres Autoscaling).

## 🏗️ Architecture

### Technology Stack
- **Frontend**: Streamlit
- **Backend**: Databricks Lakebase (Postgres Autoscaling)
- **Deployment**: Databricks Apps V2
- **Authentication**: Databricks Secrets + Static Password

### Database Schema
```sql
-- Tickets table
CREATE TABLE ticketing.tickets (
    ticket_id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Messages table
CREATE TABLE ticketing.ticket_messages (
    message_id SERIAL PRIMARY KEY,
    ticket_id INTEGER REFERENCES ticketing.tickets(ticket_id),
    message_text TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);
```

## 📁 Project Structure

```
thunderhawk-ticketing-system/
├── app.py                 # Main Streamlit application
├── app.yaml              # Databricks App configuration
├── lakebase.py           # Database connection helper
├── requirements.txt      # Python dependencies
├── setup_secrets.py      # Secret setup utility
└── README.md            # This file
```

## 🚀 Setup Instructions

### 1. Create Lakebase Database and Schema

```sql
-- Create schema
CREATE SCHEMA IF NOT EXISTS ticketing;

-- Create tickets table
CREATE TABLE ticketing.tickets (
    ticket_id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Create messages table
CREATE TABLE ticketing.ticket_messages (
    message_id SERIAL PRIMARY KEY,
    ticket_id INTEGER REFERENCES ticketing.tickets(ticket_id),
    message_text TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Create database role with password
CREATE ROLE app_user WITH LOGIN PASSWORD 'your_secure_password';

-- Grant permissions
GRANT USAGE ON SCHEMA ticketing TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ticketing TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ticketing TO app_user;
```

### 2. Configure Databricks Secret

Store your Lakebase connection URL in Databricks Secrets:

```bash
databricks secrets put-secret \
    --scope database \
    --key lakebase-url \
    --string-value "postgresql://app_user:your_password@your-host.cloud.databricks.com:5432/databricks_postgres?sslmode=require"
```

Or use the provided setup script:
```bash
python setup_secrets.py
```

### 3. Configure App Settings (Settings UI)

In the Databricks Apps Settings panel:

1. Go to **Secrets** tab
2. Add secret resource:
   - **Secret**: `database`
   - **Secret key**: `lakebase-url`
   - **Permission**: `Can read`
   - **Resource key**: `lakebase-url`

### 4. Deploy the App

```bash
databricks apps deploy thunderhawk-ticketing-system \
    --source-code-path /Workspace/Users/<your-email>/thunderhawk-ticketing-system
```

## 🎯 Features

### Core Functionality
- ✅ **View All Tickets** - Browse support tickets with message counts
- ✅ **View Messages** - Read conversation threads for each ticket
- ✅ **Create Ticket** - Submit new support requests
- ✅ **Add Message** - Reply to existing tickets
- ✅ **Update Status** - Change ticket status (open, in_progress, resolved)
- ✅ **Statistics Dashboard** - View ticket metrics and trends

### Statistics Metrics
- Total ticket count
- Status distribution (Open, In Progress, Resolved)
- Most active users
- Message statistics
- Recent activity feed

## 📚 Lessons Learned

### 1. Streamlit Port Configuration (Critical!)
**Problem**: Default Streamlit port (8080) doesn't work with Databricks Apps.

**Solution**: Always set port to 8000 in `app.yaml`:
```yaml
command:
  - streamlit
  - run
  - app.py
  - --server.port=8000  # MUST BE 8000
```

**Why**: Databricks Apps expects applications on port 8000. Using 8080 results in "App Not Available" errors even though the app shows as RUNNING.

### 2. Lakebase Connection Pattern: Secrets vs Resources
**Initial Approach (Failed)**: Resource-based connection with auto-injected env vars
```yaml
# ❌ This doesn't work well with Lakebase Autoscaling
resources:
  lakebase:
    - name: postgres-connection
      instance: your-instance
```

**Final Approach (Succeeded)**: Secret-driven connection with static password
```yaml
# ✅ This works consistently
env:
  - name: LAKEBASE_SECRET_SCOPE
    value: "database"
  - name: LAKEBASE_SECRET_KEY
    value: "lakebase-url"
```

**Why**: 
- Resource-based pattern tries to use OAuth tokens, which require dynamic token generation
- Secret-driven pattern uses static passwords stored in Databricks Secrets
- Static passwords are simpler and more reliable for app deployments

### 3. OAuth Tokens vs Static Passwords
**Challenge**: Lakebase Autoscaling requires authentication, but the method matters.

**Option A - OAuth Tokens (Complex)**:
- Requires Databricks SDK (`w.postgres.generate_database_credential()`)
- Tokens expire and need refresh logic
- More secure but adds complexity

**Option B - Static Passwords (Simple)**:
- Create Postgres role with password: `CREATE ROLE app_user WITH LOGIN PASSWORD 'password'`
- Store full connection URL in secret
- No token refresh needed
- **This is what we chose**

**Recommendation**: Use static passwords for apps unless you have specific security requirements for token rotation.

### 4. Reference Implementation is Gold
**Key Learning**: Having a working reference implementation (stock picker app) saved hours of debugging.

When stuck:
1. ✅ Copy the working pattern exactly
2. ✅ Compare file-by-file (app.yaml, lakebase.py, app.py)
3. ✅ Don't reinvent - reuse what works

**Mistake Made**: Tried to innovate with resource-based connections instead of copying the proven secrets pattern from stock picker.

### 5. File Versioning & Cleanup
**Problem**: Multiple versions of files (`app.py`, `app_old.py`, `app_v2.py`) created confusion.

**Best Practice**:
- Keep only ONE version of each file
- Delete backup files once changes are working
- Use git for versioning, not file suffixes
- Clean directory = clear mind

### 6. Incremental Testing is Essential
**Problem**: Made multiple changes at once, making debugging difficult.

**Better Approach**:
1. Change ONE thing (e.g., port to 8000)
2. Deploy and test
3. Change NEXT thing (e.g., switch to secrets)
4. Deploy and test
5. Repeat

**Debugging Checklist**:
```bash
# 1. Check app status
databricks apps get thunderhawk-ticketing-system

# 2. Check logs
databricks apps logs thunderhawk-ticketing-system

# 3. Verify secret exists
databricks secrets get-secret --scope database --key lakebase-url

# 4. Test database connection separately
python -c "import lakebase; print(lakebase.run_query('SELECT 1'))"
```

### 7. Environment Variables vs Secrets Pattern
**Key Insight**: Databricks Apps has TWO ways to pass configuration:

**Pattern 1 - Direct Environment Variables** (old resource-based):
```yaml
# App runtime looks for these in os.environ
PGHOST, PGDATABASE, PGPASSWORD, PGUSER, PGPORT
```

**Pattern 2 - Secret References** (what we use):
```yaml
env:
  - name: LAKEBASE_SECRET_SCOPE
    value: "database"
  - name: LAKEBASE_SECRET_KEY  
    value: "lakebase-url"

# App code reads secret using WorkspaceClient
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
secret = w.secrets.get_secret(scope="database", key="lakebase-url")
```

**Why Pattern 2 is Better**:
- One secret contains entire connection URL (simpler)
- No need to manage 5 separate environment variables
- Easier to update connection details
- Reusable across multiple apps

### 8. Error Messages Can Be Misleading
**Example Error**:
```
Missing required environment variables: PGHOST, PGDATABASE, PGPASSWORD
```

**What It Really Meant**: Old validation code was still in the file checking for variables that no longer exist.

**Lesson**: 
- Read error messages carefully
- Check for leftover code from previous approaches
- Validate the CURRENT implementation, not what the error assumes

### 9. Module Design Pattern
**Good Practice**: Separate database logic into `lakebase.py` module

Benefits:
- ✅ Reusable across multiple apps (stock picker, ticketing, etc.)
- ✅ Single source of truth for connection logic
- ✅ Easy to test independently
- ✅ Cleaner main app code

```python
# lakebase.py - Connection helper module
import psycopg2
from databricks.sdk import WorkspaceClient

def get_connection():
    """Single function to create DB connection"""
    # All connection logic here
    
def run_query(sql, params=None):
    """Helper for SELECT queries"""
    
def run_write(sql, params=None):
    """Helper for INSERT/UPDATE/DELETE"""
```

### 10. Databricks Apps Settings UI
**Discovery**: Apps can be configured through Settings UI, not just YAML.

**What Works in Settings UI**:
- ✅ Add/remove secret references
- ✅ View current configuration
- ✅ Manage permissions

**What Requires Redeploy**:
- ❌ Port changes (must edit app.yaml)
- ❌ Command changes (must edit app.yaml)
- ❌ App code changes (must redeploy)

## 🔧 Troubleshooting

### App Shows "App Not Available"
- Check port is set to 8000 in app.yaml
- Verify app status: `databricks apps get thunderhawk-ticketing-system`
- Check logs: `databricks apps logs thunderhawk-ticketing-system`

### Database Connection Errors
- Verify secret exists and is readable
- Test connection URL format: `postgresql://user:password@host:5432/database?sslmode=require`
- Check Postgres role has correct permissions
- Ensure password in URL is correct

### "name 'get_db_connection' is not defined"
- Make sure you're using `lakebase` module functions
- Check imports: `import lakebase`
- Use `lakebase.run_query()` and `lakebase.run_write()`

## 📖 Resources

- [Databricks Apps Documentation](https://docs.databricks.com/en/dev-tools/databricks-apps/index.html)
- [Lakebase Postgres Documentation](https://docs.databricks.com/en/lakehouse-architecture/lakebase/index.html)
- [Streamlit Documentation](https://docs.streamlit.io/)
- [Databricks Secrets](https://docs.databricks.com/en/security/secrets/index.html)

## 🎓 Key Takeaways

1. **Port 8000 is mandatory** for Databricks Apps
2. **Secrets pattern > Resource pattern** for Lakebase connections
3. **Static passwords work great** for app authentication
4. **Reference implementations save time** - copy what works
5. **Clean codebase = fewer bugs** - delete old files
6. **Test incrementally** - one change at a time
7. **Module separation** improves reusability

## 📝 License

This project is for educational purposes.

## 👤 Author

Created as part of learning Databricks platform development.

---

**Last Updated**: August 2026
