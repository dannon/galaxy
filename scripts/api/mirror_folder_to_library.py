import os
import sys
from bioblend.galaxy import GalaxyInstance
from bioblend.galaxy.libraries import LibraryClient

# ==============================================================================
# --- 1. CONFIGURATION: EDIT THESE VARIABLES ---
# ==============================================================================

# Your Galaxy server URL (e.g., https://usegalaxy.org, http://localhost:8080)
GALAXY_URL = 'https://usegalaxy.org'

# Your Galaxy API key. Find this in User -> Preferences -> Manage API Key
GALAXY_API_KEY = 'YOUR_API_KEY_HERE'

# The full path to the local directory you want to mirror
# Example for Mac/Linux: '/Users/yourname/data/motrpac'
# Example for Windows: 'C:\\Users\\yourname\\data\\motrpac'
LOCAL_ROOT_DIR = '/path/to/your/motrpac/directory'

# The name you want for the Data Library in Galaxy
LIBRARY_NAME = 'MoTrPAC Project Data'

# ==============================================================================


def get_or_create_library(gi: GalaxyInstance, name: str) -> dict:
    """
    Gets a Galaxy Data Library by name, creating it if it doesn't exist.
    Returns the library dictionary.
    """
    lib_client = LibraryClient(gi)
    # Check if a library with this name already exists
    all_libs = lib_client.get_libraries(name=name)

    if all_libs:
        print(f"Found existing library: '{name}' (ID: {all_libs[0]['id']})")
        return all_libs[0]
    else:
        print(f"Creating new library: '{name}'")
        description = f"Mirrored from local path: {LOCAL_ROOT_DIR}"
        new_lib = lib_client.create_library(name=name, description=description)
        print(f"Successfully created library: '{name}' (ID: {new_lib['id']})")
        return new_lib


def mirror_recursive(lib_client: LibraryClient, library_id: str, local_path: str, parent_folder_id: str):
    """
    Recursively walks a local directory and mirrors its structure and files
    to a folder within a Galaxy Data Library.
    """
    print(f"\nProcessing directory: {local_path}")
    try:
        for item_name in sorted(os.listdir(local_path)):
            # Ignore hidden files (like .DS_Store on macOS)
            if item_name.startswith('.'):
                continue

            full_local_path = os.path.join(local_path, item_name)

            if os.path.isdir(full_local_path):
                print(f"  - Creating folder: '{item_name}'")
                try:
                    # NOTE: This will create a new folder even if one with the same name exists.
                    # A more robust script would check for existence first.
                    new_folder = lib_client.create_folder(
                        library_id=library_id,
                        folder_name=item_name,
                        base_folder_id=parent_folder_id
                    )
                    new_folder_id = new_folder[0]['id']
                    # Recurse into the new directory
                    mirror_recursive(lib_client, library_id, full_local_path, new_folder_id)
                except Exception as e:
                    print(f"    - ERROR creating folder '{item_name}': {e}", file=sys.stderr)

            elif os.path.isfile(full_local_path):
                print(f"  - Uploading file: '{item_name}'")
                try:
                    lib_client.upload_from_local_path(
                        library_id=library_id,
                        file_local_path=full_local_path,
                        folder_id=parent_folder_id,
                        link_data_only='copy_files'  # 'copy_files' is safest
                    )
                except Exception as e:
                    print(f"    - ERROR uploading file '{item_name}': {e}", file=sys.stderr)

    except Exception as e:
        print(f"  - ERROR processing directory '{local_path}': {e}", file=sys.stderr)


def main():
    """Main function to run the script."""
    # --- 2. Validation ---
    if GALAXY_API_KEY == 'YOUR_API_KEY_HERE' or not GALAXY_API_KEY:
        print("ERROR: Please edit the script and set your GALAXY_API_KEY.", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(LOCAL_ROOT_DIR):
        print(f"ERROR: The specified local directory does not exist: {LOCAL_ROOT_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to Galaxy at: {GALAXY_URL}")
    try:
        gi = GalaxyInstance(url=GALAXY_URL, key=GALAXY_API_KEY)
        # Test connection by trying to get libraries
        gi.libraries.get_libraries(limit=1)
    except Exception as e:
        print(f"ERROR: Failed to connect to Galaxy. Check your URL and API key.", file=sys.stderr)
        print(f"Details: {e}", file=sys.stderr)
        sys.exit(1)

    print("Connection successful.")

    try:
        # --- 3. Get or Create the Data Library ---
        library = get_or_create_library(gi, LIBRARY_NAME)
        library_id = library['id']

        # The root of a data library is itself a folder. We need its ID.
        # The first item in the library contents is usually the root folder '/'.
        library_contents = LibraryClient(gi).show_library(library_id, contents=True)
        if not library_contents:
             print(f"ERROR: Library '{LIBRARY_NAME}' appears to be empty and has no root folder.", file=sys.stderr)
             sys.exit(1)

        root_folder_id = library_contents[0]['id']

        # --- 4. Start the Mirroring Process ---
        print("\nStarting the mirroring process...")
        mirror_recursive(LibraryClient(gi), library_id, LOCAL_ROOT_DIR, root_folder_id)
        print("\n--- Mirroring process finished! ---")

    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
