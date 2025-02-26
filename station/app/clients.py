from loguru import logger

from station.app.settings import Settings
from station.clients.harbor_client import HarborClient
from station.clients.airflow.client import AirflowClient
from station.clients.minio.client import MinioClient
from station.clients.central.central_client import CentralApiClient


class StationClients:
    _airflow: AirflowClient
    _harbor: HarborClient
    _minio: MinioClient
    _central: CentralApiClient

    def __init__(self, settings: Settings):
        self.settings = settings
        self.is_initialized = False

    def initialize(self):
        logger.info("Initializing clients")
        if not self.settings.is_initialized:
            logger.warning("Station settings are not initialized. Please call settings.setup() before initializing clients.")
            self.settings.setup()
        self._airflow = AirflowClient(
            airflow_api_url=self.settings.config.airflow.api_url,
            airflow_user=self.settings.config.airflow.user,
            airflow_password=self.settings.config.airflow.password.get_secret_value(),
        )
        self._harbor = HarborClient(
            api_url=self.settings.config.registry.api_url,
            username=self.settings.config.registry.user,
            password=self.settings.config.registry.password.get_secret_value(),
        )

        self._minio = MinioClient(
            minio_server=self.settings.config.minio.server_url,
            access_key=self.settings.config.minio.access_key,
            secret_key=self.settings.config.minio.secret_key,
        )

        self._minio.setup_buckets()

        self._central = CentralApiClient(
            api_url=self.settings.config.central_ui.api_url,
            robot_id=self.settings.config.central_ui.robot_id,
            robot_secret=self.settings.config.central_ui.robot_secret.get_secret_value(),
        )

        self.is_initialized = True
        logger.info("Clients initialized")

    @property
    def airflow(self) -> AirflowClient:
        if not self.is_initialized:
            logger.warning("Station clients are not initialized. Please call clients.initialize() before using clients.")
            self.initialize()
        return self._airflow

    @property
    def harbor(self) -> HarborClient:
        if not self.is_initialized:
            logger.warning("Station clients are not initialized. Please call clients.initialize() before using clients.")
            self.initialize()
        return self._harbor

    @property
    def minio(self) -> MinioClient:
        if not self.is_initialized:
            logger.warning("Station clients are not initialized. Please call clients.initialize() before using clients.")
            self.initialize()
        return self._minio

    @property
    def central(self) -> CentralApiClient:
        if not self.is_initialized:
            logger.warning("Station clients are not initialized. Please call clients.initialize() before using clients.")
            self.initialize()
        return self._central