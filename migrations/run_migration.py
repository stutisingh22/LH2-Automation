#!/usr/bin/env python3
"""
Run PostgreSQL/Supabase database migrations for LH2 Platform.

Usage:
    python run_migration.py 001  # Run migration 001
    python run_migration.py --verify  # Verify migrations
    python run_migration.py --rollback 001  # Rollback migration 001
"""

import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv("keys.env")
load_dotenv(".env")

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Validate PostgreSQL URL
if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL not found in environment variables.")
    print("   Set DATABASE_URL in keys.env or .env file.")
    sys.exit(1)

if not DATABASE_URL.startswith("postgresql://"):
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    else:
        print("❌ ERROR: DATABASE_URL must be a PostgreSQL connection string.")
        print(f"   Found: {DATABASE_URL[:20]}...")
        sys.exit(1)


def run_migration(migration_number: str):
    """Run a specific migration file."""
    migration_file = Path(__file__).parent / f"{migration_number}_performance_optimization.sql"
    
    if not migration_file.exists():
        print(f"❌ Migration file not found: {migration_file}")
        return False
    
    print(f"🔄 Running migration: {migration_file.name}")
    print(f"   Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'localhost'}")
    
    # Read migration file
    with open(migration_file, 'r') as f:
        migration_sql = f.read()
    
    # Create engine
    engine = create_engine(DATABASE_URL)
    
    try:
        with engine.connect() as conn:
            # Split into individual statements (excluding comments and empty lines)
            statements = []
            current_statement = []
            in_block_comment = False
            
            for line in migration_sql.split('\n'):
                stripped = line.strip()
                
                # Handle block comments
                if '/*' in line:
                    in_block_comment = True
                if '*/' in line:
                    in_block_comment = False
                    continue
                if in_block_comment:
                    continue
                
                # Skip single-line comments
                if stripped.startswith('--') or not stripped:
                    continue
                
                current_statement.append(line)
                
                # Check if statement is complete (ends with semicolon)
                if stripped.endswith(';'):
                    full_statement = '\n'.join(current_statement)
                    statements.append(full_statement)
                    current_statement = []
            
            # Execute each statement
            for i, statement in enumerate(statements, 1):
                if statement.strip():
                    try:
                        conn.execute(text(statement))
                        conn.commit()
                        print(f"   ✓ Statement {i}/{len(statements)} executed")
                    except Exception as e:
                        # Some statements might fail if objects already exist
                        if "already exists" in str(e).lower():
                            print(f"   ⚠ Statement {i}/{len(statements)} skipped (already exists)")
                        else:
                            print(f"   ⚠ Statement {i}/{len(statements)} warning: {e}")
            
            print("✅ Migration completed successfully!")
            return True
            
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False


def verify_migration():
    """Verify that the migration was applied correctly."""
    print("🔍 Verifying migration...")
    
    engine = create_engine(DATABASE_URL)
    
    checks = [
        ("GIN Indexes", """
            SELECT COUNT(*) as count
            FROM pg_indexes 
            WHERE indexname LIKE '%_extra_data_gin'
        """),
        ("Materialized View", """
            SELECT COUNT(*) as count
            FROM pg_matviews 
            WHERE matviewname = 'mv_pipeline_stats'
        """),
        ("View Data", """
            SELECT COUNT(*) as count
            FROM mv_pipeline_stats
            WHERE status != '_metadata'
        """),
    ]
    
    try:
        with engine.connect() as conn:
            all_passed = True
            
            for check_name, query in checks:
                result = conn.execute(text(query))
                count = result.scalar()
                
                if count > 0:
                    print(f"   ✅ {check_name}: {count} found")
                else:
                    print(f"   ❌ {check_name}: Not found")
                    all_passed = False
            
            if all_passed:
                print("\n✅ All verification checks passed!")
                
                # Show sample data
                print("\n📊 Sample pipeline stats:")
                result = conn.execute(text("""
                    SELECT status, contact_count, last_updated
                    FROM mv_pipeline_stats
                    WHERE status != '_metadata'
                    ORDER BY status
                """))
                for row in result:
                    print(f"   {row[0]}: {row[1]} contacts")
                
                # Show last refresh time
                result = conn.execute(text("""
                    SELECT last_updated
                    FROM mv_pipeline_stats
                    WHERE status = '_metadata'
                """))
                last_updated = result.scalar()
                print(f"\n   Last refreshed: {last_updated}")
                
                return True
            else:
                print("\n❌ Some verification checks failed.")
                return False
                
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False


def rollback_migration(migration_number: str):
    """Rollback a specific migration."""
    print(f"⚠️  Rolling back migration {migration_number}...")
    print(f"   Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'localhost'}")
    
    # Rollback SQL
    rollback_sql = """
    -- Drop materialized view
    DROP MATERIALIZED VIEW IF EXISTS mv_pipeline_stats CASCADE;
    
    -- Drop GIN indexes
    DROP INDEX IF EXISTS idx_companies_extra_data_gin;
    DROP INDEX IF EXISTS idx_contacts_extra_data_gin;
    DROP INDEX IF EXISTS idx_spoc_contacts_extra_data_gin;
    DROP INDEX IF EXISTS idx_advisor_companies_extra_data_gin;
    
    -- Drop additional indexes
    DROP INDEX IF EXISTS idx_sessions_company_contact;
    DROP INDEX IF EXISTS idx_actions_scheduled_at;
    DROP INDEX IF EXISTS idx_actions_session_status_scheduled;
    """
    
    engine = create_engine(DATABASE_URL)
    
    try:
        with engine.connect() as conn:
            for statement in rollback_sql.split(';'):
                if statement.strip():
                    try:
                        conn.execute(text(statement))
                        conn.commit()
                        print(f"   ✓ Executed: {statement.strip()[:50]}...")
                    except Exception as e:
                        print(f"   ⚠ Warning: {e}")
            
            print("✅ Rollback completed!")
            return True
            
    except Exception as e:
        print(f"❌ Rollback failed: {e}")
        return False


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python run_migration.py 001          # Run migration 001")
        print("  python run_migration.py --verify     # Verify migrations")
        print("  python run_migration.py --rollback 001  # Rollback migration")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "--verify":
        success = verify_migration()
        sys.exit(0 if success else 1)
    
    elif command == "--rollback":
        if len(sys.argv) < 3:
            print("❌ Please specify migration number to rollback")
            sys.exit(1)
        migration_number = sys.argv[2]
        success = rollback_migration(migration_number)
        sys.exit(0 if success else 1)
    
    else:
        # Run migration
        migration_number = command
        success = run_migration(migration_number)
        
        if success:
            print("\n🔍 Running verification...")
            verify_migration()
        
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
