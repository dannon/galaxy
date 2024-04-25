import os
import string
import subprocess
import time

from galaxy_test.base.populators import DatasetPopulator
from galaxy_test.driver import integration_util

OBJECT_STORE_HOST = os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_HOST", "127.0.0.1")
OBJECT_STORE_PORT = int(os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_PORT", 9000))
OBJECT_STORE_ACCESS_KEY = os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_ACCESS_KEY", "minioadmin")
OBJECT_STORE_SECRET_KEY = os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_SECRET_KEY", "minioadmin")
OBJECT_STORE_RUCIO_ACCOUNT = os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_RUCIO_ACCOUNT", "root")
OBJECT_STORE_RUCIO_USERNAME = os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_RUCIO_USERNAME", "rucio")
OBJECT_STORE_RUCIO_RSE_NAME = "TEST"
OBJECT_STORE_RUCIO_ACCESS = os.environ.get("GALAXY_INTEGRATION_OBJECT_STORE_RUCIO_ACCESS", "rucio")

OBJECT_STORE_CONFIG = string.Template(
    """
<object_store type="hierarchical" id="primary">
    <backends>
        <object_store id="swifty" type="generic_s3" weight="1" order="0">
            <auth access_key="${access_key}" secret_key="${secret_key}" />
            <bucket name="galaxy" use_reduced_redundancy="False" max_chunk_size="250"/>
            <connection host="${host}" port="${port}" is_secure="False" conn_path="" multipart="True"/>
            <cache path="${temp_directory}/object_store_cache" size="1000" cache_updated_data="${cache_updated_data}" />
            <extra_dir type="job_work" path="${temp_directory}/job_working_directory_swift"/>
            <extra_dir type="temp" path="${temp_directory}/tmp_swift"/>
        </object_store>
    </backends>
</object_store>
"""
)
RUCIO_OBJECT_STORE_CONFIG = string.Template(
    """
<object_store type="rucio">
    <rucio_auth account="${rucio_account}" host="http://${host}:${port}" username="${rucio_username}" password="${rucio_password}" type="userpass" />
    <rucio_connection host="http://${host}:${port}"/>
    <rucio_upload_scheme rse="${rucio_rse}" scheme="file" scope="galaxy"/>
    <rucio_download_scheme rse="${rucio_rse}" scheme="file"/>
    <cache path="${temp_directory}/object_store_cache" size="1000" cache_updated_data="${cache_updated_data}" />
    <extra_dir type="job_work" path="${temp_directory}/job_working_directory_swift"/>
    <extra_dir type="temp" path="${temp_directory}/tmp_swift"/>
</object_store>
"""
)


def start_minio(container_name):
    minio_start_args = [
        "docker",
        "run",
        "-p",
        f"{OBJECT_STORE_PORT}:9000",
        "-d",
        "--name",
        container_name,
        "--rm",
        "minio/minio:latest",
        "server",
        "/data",
    ]
    subprocess.check_call(minio_start_args)


def wait_rucio_ready(container_name):
    rucio_check_args = [
        "docker",
        "exec",
        container_name,
        "rucio",
        "list-rses",
    ]
    timeout = 30
    start_time = time.time()
    while True:
        try:
            rse = subprocess.check_output(rucio_check_args).decode("utf-8").strip()
            if rse == OBJECT_STORE_RUCIO_RSE_NAME:
                return
        except subprocess.CalledProcessError:
            pass
        if time.time() - start_time >= timeout:
            raise TimeoutError(rse)
        time.sleep(1)


def start_rucio(container_name):
    rucio_start_args = [
        "docker",
        "run",
        "-p",
        f"{OBJECT_STORE_PORT}:80",
        "-d",
        "--name",
        container_name,
        "--rm",
        "code.ornl.gov:4567/ndip/public-docker/rucio:1.29.8",
    ]
    subprocess.check_call(rucio_start_args)

    wait_rucio_ready(container_name)


def stop_docker(container_name):
    subprocess.check_call(["docker", "rm", "-f", container_name])


class BaseObjectStoreIntegrationTestCase(integration_util.IntegrationTestCase, integration_util.ConfiguresObjectStores):
    dataset_populator: DatasetPopulator
    framework_tool_and_types = True

    def setUp(self):
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)


def get_files(directory):
    for rel_directory, _, files in os.walk(directory):
        for file_ in files:
            yield os.path.join(rel_directory, file_)


def files_count(directory):
    return sum(1 for _ in get_files(directory))


@integration_util.skip_unless_docker()
class BaseSwiftObjectStoreIntegrationTestCase(BaseObjectStoreIntegrationTestCase):
    object_store_cache_path: str

    @classmethod
    def setUpClass(cls):
        cls.container_name = f"{cls.__name__}_container"
        start_minio(cls.container_name)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        stop_docker(cls.container_name)
        super().tearDownClass()

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        temp_directory = cls._test_driver.mkdtemp()
        cls.object_stores_parent = temp_directory
        cls.object_store_cache_path = f"{temp_directory}/object_store_cache"
        config_path = os.path.join(temp_directory, "object_store_conf.xml")
        config["object_store_store_by"] = "uuid"
        config["metadata_strategy"] = "extended"
        config["outputs_to_working_directory"] = True
        config["retry_metadata_internally"] = False
        with open(config_path, "w") as f:
            f.write(
                OBJECT_STORE_CONFIG.safe_substitute(
                    {
                        "temp_directory": temp_directory,
                        "host": OBJECT_STORE_HOST,
                        "port": OBJECT_STORE_PORT,
                        "access_key": OBJECT_STORE_ACCESS_KEY,
                        "secret_key": OBJECT_STORE_SECRET_KEY,
                        "cache_updated_data": cls.updateCacheData(),
                    }
                )
            )
        config["object_store_config_file"] = config_path

    def setUp(self):
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)

    @classmethod
    def updateCacheData(cls):
        return True


@integration_util.skip_unless_docker()
class BaseRucioObjectStoreIntegrationTestCase(BaseObjectStoreIntegrationTestCase):
    object_store_cache_path: str

    @classmethod
    def setUpClass(cls):
        cls.container_name = f"{cls.__name__}_container"
        start_rucio(cls.container_name)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        stop_docker(cls.container_name)
        super().tearDownClass()

    @classmethod
    def handle_galaxy_config_kwds(cls, config):
        super().handle_galaxy_config_kwds(config)
        temp_directory = cls._test_driver.mkdtemp()
        cls.object_stores_parent = temp_directory
        cls.object_store_cache_path = f"{temp_directory}/object_store_cache"
        config_path = os.path.join(temp_directory, "object_store_conf.xml")
        config["object_store_store_by"] = "uuid"
        config["metadata_strategy"] = "extended"
        config["outputs_to_working_directory"] = True
        config["retry_metadata_internally"] = False
        # Rucio client requires a config file to exist on disk. This is ugly,
        # but we have to live with it for now. An issue is created: https://github.com/rucio/rucio/issues/6410
        rucio_config_path = os.path.join(temp_directory, "rucio.cfg")
        env_file = os.path.join(temp_directory, "env_set.sh")
        with open(env_file, "w") as f:
            f.write(f"export RUCIO_CONFIG={rucio_config_path}\n")
        config["environment_setup_file"] = env_file
        with open(rucio_config_path, "w") as f:
            f.write("[client]\n")
            f.write(f"rucio_host = http://{OBJECT_STORE_HOST}:{OBJECT_STORE_PORT}\n")
            f.write(f"auth_host = http://{OBJECT_STORE_HOST}:{OBJECT_STORE_PORT}\n")
            f.write(f"account = {OBJECT_STORE_RUCIO_ACCOUNT}\n")
            f.write("auth_type = userpass\n")
            f.write(f"username = {OBJECT_STORE_RUCIO_USERNAME}\n")
            f.write(f"password = {OBJECT_STORE_RUCIO_ACCESS}\n")
        os.environ["RUCIO_CONFIG"] = rucio_config_path
        with open(config_path, "w") as f:
            f.write(
                RUCIO_OBJECT_STORE_CONFIG.safe_substitute(
                    {
                        "temp_directory": temp_directory,
                        "host": OBJECT_STORE_HOST,
                        "port": OBJECT_STORE_PORT,
                        "rucio_account": OBJECT_STORE_RUCIO_ACCOUNT,
                        "rucio_username": OBJECT_STORE_RUCIO_USERNAME,
                        "rucio_password": OBJECT_STORE_RUCIO_ACCESS,
                        "rucio_rse": OBJECT_STORE_RUCIO_RSE_NAME,
                        "cache_updated_data": cls.updateCacheData(),
                    }
                )
            )

        config["object_store_config_file"] = config_path

    def setUp(self):
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)

    @classmethod
    def updateCacheData(cls):
        return True
