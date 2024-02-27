import subprocess
import re
import os

def run_build_and_attempt_fixes(build_command, working_directory):
    errors_fixed = False
    attempt = 0

    while True:
        attempt += 1
        print(f"Attempt #{attempt}: Running build...")

        try:
            # Attempt to run the build command
            result = subprocess.run(build_command, shell=True, check=True, capture_output=True, text=True, cwd=working_directory)
            print("Build completed successfully!")
            print(result.stdout)
            return  # Exit the loop and function if build is successful
        except subprocess.CalledProcessError as e:
            print("Build failed. Analyzing errors...")
            error_output = e.stderr

            print(error_output)

            # Define regex patterns for both error types
            pattern1 = r"Error: \[vite:load-fallback\] Could not load (.+) \(imported by (.+)\): ENOENT"
            pattern2 = r"RollupError: Could not resolve \"(.+)\" from \"(.+)\""
            
            errors = re.findall(pattern1, error_output) + re.findall(pattern2, error_output)

            if not errors:
                print("No recognized errors found, stopping attempts.")
                return

            errors_fixed_in_this_pass = False

            for missing_file, importing_file_rel in errors:
                print(missing_file, importing_file_rel, working_directory)
                component_name = os.path.basename(missing_file) if ":" not in missing_file else missing_file.replace('./', '')
                importing_file_path = os.path.join(working_directory, importing_file_rel)


                if os.path.exists(importing_file_path):
                    # Prepare the sed command to fix the import statement by appending '.vue' to the component import
                    sed_command = f"sed -i 's|{component_name}\"|{component_name}.vue\"|g' {importing_file_path}"
                    sed_result = subprocess.run(sed_command, shell=True)
                    if sed_result.returncode == 0:
                        errors_fixed_in_this_pass = True
                        print(f"Corrected import statement for {component_name} in {importing_file_path}.")
                    else:
                        print(f"Failed to correct import statement for {component_name} in {importing_file_path}.")
                else:
                    print(f"Warning: The file {importing_file_path} was not found.")

            if not errors_fixed_in_this_pass:
                print("No errors were fixed in this pass, stopping attempts.")
                return

if __name__ == "__main__":
    build_command = "yarn run vite build"
    working_directory = "/home/dannon/work/galaxy/client"  # Adjust the path as necessary
    run_build_and_attempt_fixes(build_command, working_directory)
