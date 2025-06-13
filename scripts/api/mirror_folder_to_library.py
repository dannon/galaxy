#!/usr/bin/env python3
"""
Mirror a local directory structure to a Galaxy Data Library.

DESCRIPTION:
    This script recursively mirrors a local directory structure to a Galaxy Data Library,
    preserving the exact folder hierarchy and uploading all files. It's designed to be
    idempotent - you can run it multiple times safely without creating duplicates.

USAGE:
    # Basic usage
    python mirror_folder_to_library.py /path/to/data \\
        --url https://galaxy.example.com \\
        --api-key YOUR_API_KEY

    # Preview changes without uploading
    python mirror_folder_to_library.py /path/to/data \\
        --url https://galaxy.example.com \\
        --api-key YOUR_API_KEY \\
        --dry-run

    # Custom library name
    python mirror_folder_to_library.py /path/to/data \\
        --url https://galaxy.example.com \\
        --api-key YOUR_API_KEY \\
        --library-name "My Research Data"

    # Overwrite existing files
    python mirror_folder_to_library.py /path/to/data \\
        --url https://galaxy.example.com \\
        --api-key YOUR_API_KEY \\
        --force

FEATURES:
    - Idempotent: Safe to run multiple times without creating duplicates
    - Local cache: Tracks uploaded items in .galaxy_mirror_cache_*.json
    - Dry-run mode: Preview changes before uploading
    - Force mode: Overwrite existing files (folders are never overwritten)
    - Progress tracking: Shows what's being created vs. what already exists

REQUIREMENTS:
    - Python 3.6+
    - bioblend library: pip install bioblend
    - Galaxy API key (User → Preferences → Manage API Key)
    
    # Alternative: Use uvx to handle dependencies automatically
    uvx --with bioblend python mirror_folder_to_library.py [args...]

CACHE FILES:
    The script creates a cache file in your source directory:
    .galaxy_mirror_cache_{library_name}.json
    
    This tracks what has been uploaded to avoid duplicates. You can:
    - Delete it to start fresh
    - Use --refresh-cache to rebuild it
    - It's automatically ignored during uploads

EXAMPLES:
    # Upload research data to Galaxy
    python mirror_folder_to_library.py ~/research/experiment1 \\
        --url https://usegalaxy.org \\
        --api-key abc123def456

    # Preview upload to local Galaxy instance  
    python mirror_folder_to_library.py ./data \\
        --url http://localhost:8080 \\
        --api-key your_key_here \\
        --dry-run

    # Force re-upload everything
    python mirror_folder_to_library.py ./data \\
        --url http://localhost:8080 \\
        --api-key your_key_here \\
        --refresh-cache --force
"""

import os
import sys
import json
import argparse
from bioblend.galaxy import GalaxyInstance
from bioblend.galaxy.libraries import LibraryClient


def load_cache(cache_file):
    """
    Load the local cache of Galaxy library structure.
    Returns empty dict if cache doesn't exist or is invalid.
    """
    if not os.path.exists(cache_file):
        return {'folders': {}, 'files': {}}
    
    try:
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)
            # Ensure required keys exist
            if 'folders' not in cache_data:
                cache_data['folders'] = {}
            if 'files' not in cache_data:
                cache_data['files'] = {}
            return cache_data
    except (json.JSONDecodeError, IOError, OSError):
        return {'folders': {}, 'files': {}}


def save_cache(cache_file, cache_data):
    """
    Save the local cache of Galaxy library structure.
    """
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
    except IOError as e:
        print(f"WARNING: Could not save cache file: {e}", file=sys.stderr)


def get_cache_key(library_id, parent_folder_id, item_name):
    """
    Generate a cache key for a folder or file.
    """
    return f"{library_id}:{parent_folder_id}:{item_name}"


def find_existing_folder(cache_data, library_id, parent_folder_id, folder_name):
    """
    Check if a folder exists in the cache.
    Returns the folder ID if found, None otherwise.
    """
    cache_key = get_cache_key(library_id, parent_folder_id, folder_name)
    folder_data = cache_data.get('folders', {}).get(cache_key)
    return folder_data.get('id') if folder_data else None


def find_existing_file(cache_data, library_id, parent_folder_id, filename):
    """
    Check if a file exists in the cache.
    Returns True if found, False otherwise.
    """
    cache_key = get_cache_key(library_id, parent_folder_id, filename)
    return cache_key in cache_data.get('files', {})


def get_or_create_library(gi, name, local_path, dry_run):
    """
    Find an existing library by name, or create a new one.
    In dry-run mode, just simulate the creation.
    """
    lib_client = LibraryClient(gi)
    all_libs = lib_client.get_libraries(name=name)

    if all_libs:
        print(f"Found existing library: '{name}' (ID: {all_libs[0]['id']})")
        return all_libs[0]
    else:
        if dry_run:
            print(f"[DRY RUN] Would create new library: '{name}'")
            return {"id": "DRY_RUN_LIB_ID", "name": name}
        else:
            print(f"Creating new library: '{name}'")
            description = f"Mirrored from: {local_path}"
            new_lib = lib_client.create_library(name=name, description=description)
            print(f"Created library: '{name}' (ID: {new_lib['id']})")
            return new_lib


def mirror_recursive(lib_client, library_id, local_path, parent_folder_id, dry_run, 
                    force=False, cache_data=None):
    """
    Walk through a directory and recreate its structure in Galaxy.
    Upload all files and create all subdirectories recursively.
    """
    if cache_data is None:
        cache_data = {'folders': {}, 'files': {}}
    print(f"\nProcessing: {local_path}")
    try:
        for item_name in sorted(os.listdir(local_path)):
            # Skip hidden files and cache files
            if item_name.startswith("."):
                continue

            full_local_path = os.path.join(local_path, item_name)

            if os.path.isdir(full_local_path):
                existing_folder_id = find_existing_folder(cache_data, library_id, parent_folder_id, item_name)
                
                if dry_run:
                    if existing_folder_id:
                        print(f"  - [DRY RUN] Folder exists: '{item_name}'")
                        folder_id = existing_folder_id
                    else:
                        print(f"  - [DRY RUN] Would create folder: '{item_name}'")
                        # Use dummy folder ID for dry run
                        folder_id = f"{parent_folder_id}_dummy_{item_name}"
                    mirror_recursive(lib_client, library_id, full_local_path, folder_id, dry_run, force, cache_data)
                else:
                    if existing_folder_id:
                        print(f"  - Folder exists: '{item_name}'")
                        folder_id = existing_folder_id
                    else:
                        print(f"  - Creating folder: '{item_name}'")
                        try:
                            new_folder = lib_client.create_folder(
                                library_id=library_id,
                                folder_name=item_name,
                                base_folder_id=parent_folder_id,
                            )
                            # Galaxy API typically returns a list with folder info
                            if isinstance(new_folder, list) and new_folder:
                                folder_id = new_folder[0]["id"]
                            elif hasattr(new_folder, 'get') and new_folder.get("id"):
                                folder_id = new_folder["id"]
                            else:
                                print(f"    - WARNING: Unexpected response format: {new_folder}")
                                continue
                            
                            # Add to cache
                            cache_key = get_cache_key(library_id, parent_folder_id, item_name)
                            cache_data['folders'][cache_key] = {
                                'id': folder_id,
                                'name': item_name,
                                'parent_id': parent_folder_id
                            }
                        except Exception as e:
                            print(f"    - ERROR creating folder '{item_name}': {e}", file=sys.stderr)
                            continue
                    
                    mirror_recursive(lib_client, library_id, full_local_path, folder_id, dry_run, force, cache_data)

            elif os.path.isfile(full_local_path):
                file_exists = find_existing_file(cache_data, library_id, parent_folder_id, item_name)
                
                if dry_run:
                    if file_exists:
                        if force:
                            print(f"  - [DRY RUN] Would overwrite file: '{item_name}'")
                        else:
                            print(f"  - [DRY RUN] File exists: '{item_name}'")
                    else:
                        print(f"  - [DRY RUN] Would upload file: '{item_name}'")
                else:
                    if file_exists and not force:
                        print(f"  - File exists: '{item_name}'")
                    else:
                        if file_exists and force:
                            print(f"  - Overwriting file: '{item_name}'")
                        else:
                            print(f"  - Uploading file: '{item_name}'")
                        
                        try:
                            lib_client.upload_file_from_local_path(
                                library_id=library_id,
                                file_local_path=full_local_path,
                                folder_id=parent_folder_id,
                            )
                            # Add file to cache
                            cache_key = get_cache_key(library_id, parent_folder_id, item_name)
                            cache_data['files'][cache_key] = {
                                'name': item_name,
                                'parent_id': parent_folder_id,
                                'local_path': full_local_path
                            }
                        except Exception as e:
                            print(f"    - ERROR uploading file '{item_name}': {e}", file=sys.stderr)

    except Exception as e:
        print(f"  - ERROR processing directory '{local_path}': {e}", file=sys.stderr)


def main():
    """Main function to parse arguments and run the script."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "local_root_dir",
        help="Path to the local directory you want to mirror to Galaxy.",
    )
    parser.add_argument(
        "-u",
        "--url",
        required=True,
        help="Galaxy instance URL (e.g., https://usegalaxy.org).",
    )
    parser.add_argument(
        "-k",
        "--api-key",
        required=True,
        help="Your Galaxy API key (User -> Preferences -> Manage API Key).",
    )
    parser.add_argument(
        "-n",
        "--library-name",
        help="Name for the Galaxy Data Library (defaults to directory name).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would happen without making any changes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (folders are never overwritten).",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing cache and rebuild from scratch.",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.local_root_dir):
        print(f"ERROR: Directory not found: {args.local_root_dir}", file=sys.stderr)
        sys.exit(1)

    library_name = args.library_name or os.path.basename(os.path.abspath(args.local_root_dir))

    print("=== Configuration ===")
    print(f"Galaxy URL:      {args.url}")
    print(f"Local Directory: {args.local_root_dir}")
    print(f"Library Name:    {library_name}")
    if args.dry_run:
        print("Mode:            DRY RUN (preview only)")
    print("=====================")

    try:
        print(f"\nConnecting to Galaxy at: {args.url}")
        gi = GalaxyInstance(url=args.url, key=args.api_key)
        gi.users.get_current_user()
    except Exception as e:
        print(f"ERROR: Cannot connect to Galaxy. Check URL and API key: {e}", file=sys.stderr)
        sys.exit(1)
    print("Connected successfully.")

    try:
        library = get_or_create_library(gi, library_name, args.local_root_dir, args.dry_run)
        library_id = library["id"]

        lib_client = LibraryClient(gi)
        root_folder_id = "/"

        # Get the actual root folder ID for real operations
        if not args.dry_run and library_id != "DRY_RUN_LIB_ID":
            library_contents = lib_client.show_library(library_id, contents=True)
            if not library_contents:
                print(f"ERROR: Library '{library_name}' has no root folder.", file=sys.stderr)
                sys.exit(1)
            root_folder_id = library_contents[0]["id"]
        elif args.dry_run and library_id != "DRY_RUN_LIB_ID":
            # For dry run with existing library, still get the root folder
            library_contents = lib_client.show_library(library_id, contents=True)
            if library_contents:
                root_folder_id = library_contents[0]["id"]

        # Load cache file from source directory
        cache_filename = os.path.join(args.local_root_dir, f".galaxy_mirror_cache_{library_name}.json")
        if args.refresh_cache and os.path.exists(cache_filename):
            os.remove(cache_filename)
            print("Removed existing cache file")
        
        cache_data = load_cache(cache_filename)
        folder_count = len(cache_data.get('folders', {}))
        file_count = len(cache_data.get('files', {}))
        if folder_count > 0 or file_count > 0:
            print(f"Found existing cache: {folder_count} folders, {file_count} files")

        mirror_recursive(lib_client, library_id, args.local_root_dir, root_folder_id, args.dry_run, args.force, cache_data)

        # Update cache with what we've processed
        if not args.dry_run:
            save_cache(cache_filename, cache_data)

        print("\n=== Process Complete! ===")

    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
