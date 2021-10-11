import tarfile
import time
import docker
import os
import io
from io import BytesIO
from docker.models.containers import Container
import asyncio
from station.clients.minio import MinioClient

class aachenClientLocalTrain:

    def __init__(self, context):
        self.docker_client = docker.from_env()
        self.minio_client = MinioClient(minio_server="127.0.0.1:9000", access_key="minio_admin",
                                        secret_key="minio_admin")
        self.train_state_dict =  context
        self.build_dir=  "./temp/"
        self.results =  "./result/"
        self.repo = f'aachen_{self.train_state_dict["endpoint"].lower()}_train'

    def build_image(self):

        requirements_file = self.minio_client.get_file(self.train_state_dict["bucket"],
                                                       self.train_state_dict["requirements"])
        requirements_file = BytesIO(requirements_file)
        with open(self.build_dir+self.train_state_dict["requirements"], 'wb') as f:
            f.write(requirements_file.getbuffer())

        dockerignore_file = self.minio_client.get_file(self.train_state_dict["bucket"],
                                                       self.train_state_dict["dockerignore"])
        dockerignore_file = BytesIO(dockerignore_file)
        with open(self.build_dir+self.train_state_dict["dockerignore"], 'wb') as f:
            f.write(dockerignore_file.getbuffer())

        docker_file = self.minio_client.get_file(self.train_state_dict["bucket"], self.train_state_dict["dockerfile"])
        docker_file = BytesIO(docker_file)
        with open(self.build_dir+self.train_state_dict["dockerfile"], 'wb') as f:
            f.write(docker_file.getbuffer())
        print(self.train_state_dict["label"])

        image, logs = self.docker_client.images.build(path=self.build_dir,labels = self.train_state_dict["label"] ,rm=True)
        container = self.docker_client.containers.create(image.id)

        endpoint = self.minio_client.get_file(self.train_state_dict["bucket"], self.train_state_dict["endpoint"])

        tarfile_name = f'{self.train_state_dict["endpoint"]}.tar'
        with tarfile.TarFile(tarfile_name, 'w') as tar:
            data_file = tarfile.TarInfo(name=self.train_state_dict["endpoint"])
            data_file.size = len(endpoint)
            tar.addfile(data_file, io.BytesIO(endpoint))

        with open(tarfile_name, 'rb') as fd:
            respons = container.put_archive("/", fd)
            container.wait()
            print(respons)
        container.commit(repository=self.repo, tag="latest")
        container.wait()
        print(f"build {container.labels=}")

    def run_train(self):

        container = self.docker_client.containers.run(self.repo,environment= self.train_state_dict["label"],  detach=True)
        print(f"run {container.labels=}")
        exit_code = container.wait()["StatusCode"]
        print(f"{exit_code} run fin")
        f = open(f'results.tar', 'wb')
        results = container.get_archive('opt/pht_results')
        bits, stat = results
        for chunk in bits:
            f.write(chunk)
        f.close()

    def save_results(self):
        with open(f'results.tar', 'rb') as results_tar:
            asyncio.run(
                self.minio_client.store_files(bucket=self.train_state_dict["bucket"], name="results.tar", file=results_tar))


if __name__ == '__main__':
    context = {
      "dockerfile": "Dockerfile",
      "endpoint": "mukoWithPython.py",
      "requirements": "requirements.txt",
      "dockerignore": ".dockerignore",
      "bucket": "aachentrain",
      "build_dir": "./temp/",
      "label": {"FHIR_SERVER": "https://blaze-fhir.personalhealthtrain.de/fhir", "FHIR_PORT": "443",
                  "FHIR_TOKEN": "eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJ5VmMwcnVQZjdrMDgxN2JWMWF0ZFoycWpJUUFqYnR3RUpiZklvZ3k3aElzIn0.eyJleHAiOjE2MzM5ODMzMTEsImlhdCI6MTYzMzk0NzMxMSwianRpIjoiZmQyMTBhZDktNzA1OS00YjE1LTkwNzAtYzM2YjIxNWE3ZDE2IiwiaXNzIjoiaHR0cHM6Ly9rZXljbG9hay1waHQudGFkYTVoaS5uZXQvYXV0aC9yZWFsbXMvYmxhemUiLCJzdWIiOiI3MmE3ZjM3ZS1iMzNmLTQ5MDgtOWFkOS0zM2JlMGQ0YzE2MjAiLCJ0eXAiOiJCZWFyZXIiLCJhenAiOiJhY2NvdW50IiwiYWNyIjoiMSIsInJlc291cmNlX2FjY2VzcyI6eyJhY2NvdW50Ijp7InJvbGVzIjpbIm1hbmFnZS1hY2NvdW50Iiwidmlldy1hcHBsaWNhdGlvbnMiLCJ2aWV3LWNvbnNlbnQiLCJtYW5hZ2UtYWNjb3VudC1saW5rcyIsImRlbGV0ZS1hY2NvdW50IiwibWFuYWdlLWNvbnNlbnQiLCJ2aWV3LXByb2ZpbGUiXX19LCJzY29wZSI6ImVtYWlsIHByb2ZpbGUiLCJjbGllbnRJZCI6ImFjY291bnQiLCJjbGllbnRIb3N0IjoiMTkyLjE2OC4wLjEiLCJlbWFpbF92ZXJpZmllZCI6ZmFsc2UsInByZWZlcnJlZF91c2VybmFtZSI6InNlcnZpY2UtYWNjb3VudC1hY2NvdW50IiwiY2xpZW50QWRkcmVzcyI6IjE5Mi4xNjguMC4xIn0.buXJBegDbLAb-g5qshbIua9jdN763TJMlSQdLOli4fSgZ4-7dMRNfU7FR5erTdMKwdNzHgTm-emBOxr18PEUCWBSFznY2qYu4Fq2r7SVYrEvazy26BhOc5QGBjyPBD46DGvghlLj1dK8YSBfWvZvg454xafieNcJAJSGYs6fuHjENNDP1Z5wL2lC2O12oaSILnva64XfgurEJj9EE0jpK4OtbJV45XIsZvEmc9dtycdczlhuieYKoRD4eHZwJObw_JjXykeO93CL65nIX380e61ZQ4FcirX5plpjlikI-a6L78yd5WqN-31hMs9ZfaxInr8kdcG32fDqroIBdEH3AQ"}    }
    aachen_client_local_train = aachenClientLocalTrain(context)
    aachen_client_local_train.build_image()
    aachen_client_local_train.run_train()
    aachen_client_local_train.save_results()
