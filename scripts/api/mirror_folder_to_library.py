#!/usr/bin/env python3
"""
Mirror a local directory structure to a Galaxy Data Library.

This script walks through a local directory and recreates the same folder
structure in a Galaxy Data Library, uploading all files along the way.
It will create a new library or use an existing one with the same name.

Use --dry-run to preview what would happen without making any changes.
"""

import os
import sys
import argparse
from bioblend.galaxy import GalaxyInstance
from bioblend.galaxy.libraries import LibraryClient


def get_or_create_library(gi: GalaxyInstance, name: str, local_path: str, dry_run: bool) -> dict:
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


def mirror_recursive(lib_client: LibraryClient, library_id: str, local_path: str, 
                    parent_folder_id: str, dry_run: bool):
    """
    Walk through a directory and recreate its structure in Galaxy.
    Upload all files and create all subdirectories recursively.
    """
    print(f"\nProcessing: {local_path}")
    try:
        for item_name in sorted(os.listdir(local_path)):
            if item_name.startswith("."):
                continue

            full_local_path = os.path.join(local_path, item_name)

            if os.path.isdir(full_local_path):
                if dry_run:
                    print(f"  - [DRY RUN] Would create folder: '{item_name}'")
                    dummy_folder_id = os.path.join(parent_folder_id, item_name)
                    mirror_recursive(lib_client, library_id, full_local_path, dummy_folder_id, dry_run)
                else:
                    print(f"  - Creating folder: '{item_name}'")
                    try:
                        new_folder = lib_client.create_folder(
                            library_id=library_id,
                            folder_name=item_name,
                            base_folder_id=parent_folder_id,
                        )
                        # Handle different response formats from Galaxy API
                        if isinstance(new_folder, list) and len(new_folder) > 0:
                            new_folder_id = new_folder[0]["id"]
                        elif isinstance(new_folder, dict) and "id" in new_folder:
                            new_folder_id = new_folder["id"]
                        else:
                            raise ValueError(f"Unexpected folder creation response: {new_folder}")
                            
                        mirror_recursive(lib_client, library_id, full_local_path, new_folder_id, dry_run)
                    except Exception as e:
                        print(f"    - ERROR creating folder '{item_name}': {e}", file=sys.stderr)

            elif os.path.isfile(full_local_path):
                if dry_run:
                    print(f"  - [DRY RUN] Would upload file: '{item_name}'")
                else:
                    print(f"  - Uploading file: '{item_name}'")
                    try:
                        lib_client.upload_file_from_local_path(
                            library_id=library_id,
                            file_local_path=full_local_path,
                            folder_id=parent_folder_id,
                        )
                    except Exception as e:
                        print(
                            f"    - ERROR uploading file '{item_name}': {e}",
                            file=sys.stderr,
                        )

    except Exception as e:
        print(f"  - ERROR processing directory '{local_path}': {e}", file=sys.stderr)


def main():
    """Main function to parse arguments and run the script."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # --- Arguments ---
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

        mirror_recursive(lib_client, library_id, args.local_root_dir, root_folder_id, args.dry_run)

        print("\n=== Process Complete! ===")

    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
