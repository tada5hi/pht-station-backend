import json
import os
import pprint
import sys
import time
from typing import Tuple

import click
import docker
from rich.console import Console

from station.clients.central.central_client import CentralApiClient
from station.ctl.config import validate_config, fix_config
from station.ctl.config.command import render_config
from station.ctl.config.validators import ConfigItemValidationStatus
from station.ctl.constants import Icons, PHTDirectories, PHTImages
from station.ctl.install.docker import setup_docker, download_docker_images
from station.ctl.install import templates
from station.ctl.install.fs import check_create_pht_dirs


@click.command(help="Install the station software based on the configuration file.")
@click.option('--install-dir',
              type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Install location for station software. Defaults to current working directory.')
@click.option('--host-path', type=str, help='Host path for containerized execution of the installer', required=False)
@click.pass_context
def install(ctx, install_dir, host_path):
    # validate configuration before installing
    click.echo('Validating configuration... ', nl=False)
    ctx.obj["host_path"] = host_path
    validation_results, table = validate_config(ctx.obj, host_path=host_path)
    issues = [result for result in validation_results if result.status != ConfigItemValidationStatus.VALID]

    if issues:
        click.echo(Icons.CROSS.value)
        console = Console()
        console.print(table)
        click.confirm(f"Station configuration is invalid. Please fix the errors displayed above. \n"
                      f"Would you like to fix the configuration now?", abort=True)
        station_config = fix_config(ctx.obj, ctx.obj, validation_results)
        ctx.obj = station_config
        render_config(ctx.obj, ctx.obj["config_path"])

    else:
        click.echo(Icons.CHECKMARK.value)

    if not install_dir:
        install_dir = os.getcwd()

    ctx.obj['install_dir'] = install_dir
    host_path = ctx.obj.get("host_path")
    click.echo('Installing station software to {}'.format(host_path if host_path else install_dir))
    # ensure file system is set up
    check_create_pht_dirs(install_dir)

    # get credentials for registry
    reg_credentials = _request_registry_credentials(ctx)

    ctx.obj["registry"]["project"] = reg_credentials["external_name"]

    # setup docker volumes and networks
    setup_docker()
    # download docker images
    download_docker_images(ctx)

    # setup_auth_server
    _setup_auth_server(ctx)

    # render templates according to configuration and store output paths in configuration object
    ctx.obj["init_sql_path"] = write_init_sql(ctx)
    traefik_config_path, router_config_path = write_traefik_configs(ctx)
    ctx.obj["traefik_config_path"] = traefik_config_path
    ctx.obj["router_config_path"] = router_config_path
    ctx.obj["airflow_config_path"] = write_airflow_config(ctx)

    # render the updated configuration file
    render_config(ctx.obj, ctx.obj['config_path'])

    # render the final compose template
    write_compose_file(ctx)


def _request_registry_credentials(ctx):
    click.echo('Requesting registry credentials from central api... ', nl=False)
    url = ctx.obj['central']['api_url']
    client = ctx.obj['central']['robot_id']
    secret = ctx.obj['central']['robot_secret']
    client = CentralApiClient(url, client, secret)

    credentials = client.get_registry_credentials(ctx.obj["station_id"])
    click.echo(Icons.CHECKMARK.value)

    return credentials


def _setup_auth_server(ctx):
    click.echo('Setting up auth server... ', nl=False)
    client = docker.from_env()

    auth_image = f"{PHTImages.AUTH.value}:{ctx.obj['version']}"
    command = "start"

    if ctx.obj.get("host_path"):
        writable_dir = os.path.join(ctx.obj['host_path'], str(PHTDirectories.SERVICE_DATA_DIR.value), "auth")
    else:
        writable_dir = os.path.join(ctx.obj['install_dir'], str(PHTDirectories.SERVICE_DATA_DIR.value), "auth")

    auth_volumes = {
        str(writable_dir): {
            "bind": "/usr/src/app/writable",
            "mode": "rw"
        }
    }
    environment = {
        "ADMIN_USER": "admin",
        "ADMIN_PASSWORD": ctx.obj['admin_password'],
        "NODE_ENV": "production",
        "PUBLIC_URL": "https://" + ctx.obj["https"]["domain"] + "/auth",
        "AUTHORIZATION_REDIRECT_URL": "https://" + ctx.obj["https"]["domain"],
        # "TYPEORM_CONNECTION": "postgres",
        # "TYPEORM_HOST": "postgres",
        # "TYPEORM_USERNAME": ctx.obj["db"]["admin_user"],
        # "TYPEORM_PASSWORD": ctx.obj["db"]["admin_password"],
        # "TYPEORM_DATABASE": "auth",
        # "TYPEORM_PORT": 5432,
        # "TYPEORM_SYNCHRONIZE": "false",
        # "TYPEORM_LOGGING": "true",
    }
    container = client.containers.run(auth_image,
                                      command,
                                      remove=False,
                                      detach=True,
                                      environment=environment,
                                      volumes=auth_volumes)
    # exit_code = container.wait()
    logs = client.containers.get(container.id).logs(stream=True, follow=True)

    try:
        while True:
            line = next(logs).decode("utf-8")
            if "Startup completed." in line:
                # todo improve this
                time.sleep(5)
                break
    except StopIteration:
        print(f'log stream ended for {container.id}')
    except KeyboardInterrupt:
        print('interrupted!')
    # print(logs.decode())
    retry_delays = [1, 5, 10]
    seed_path = os.path.join(ctx.obj['install_dir'], str(PHTDirectories.SERVICE_DATA_DIR.value), "auth", "seed.json")
    for i, delay in enumerate(retry_delays):
        try:
            with open(seed_path, "r") as f:
                seed = json.load(f)
            break
        except FileNotFoundError:
            if i == len(retry_delays) - 1:
                raise FileNotFoundError("Seed file not found")
            else:
                click.echo(f"Seed file not found at {seed_path}. Retrying in {delay} seconds..")
                time.sleep(delay)

    robot_id = seed["robotId"]
    robot_secret = seed["robotSecret"]
    # print("".join(output))

    if not (robot_id and robot_secret):
        click.echo(Icons.CROSS.value)
        click.echo("Failed to setup auth server", err=True)
        click.echo(logs, err=True)
        raise Exception("Could not get robot credentials from auth server")

    else:

        auth = {
            "robot_id": robot_id,
            "robot_secret": robot_secret,
        }
        ctx.obj["auth"] = auth

        client.containers.get(container.id).stop()
        click.echo(Icons.CHECKMARK.value)


def write_init_sql(ctx) -> str:
    click.echo('Setting up database... ', nl=False)
    try:

        db_config = ctx.obj['db']
        init_sql_path = os.path.join(
            ctx.obj['install_dir'],
            str(PHTDirectories.SETUP_SCRIPT_DIR.value),
            'init.sql'
        )
        with open(init_sql_path, 'w') as f:
            f.write(templates.render_init_sql(db_user=db_config["admin_user"]))

        click.echo(Icons.CHECKMARK.value)
        # if host path is given, return the path to the init sql file on the host
        if ctx.obj.get("host_path"):
            return os.path.join(ctx.obj['host_path'], str(PHTDirectories.SETUP_SCRIPT_DIR.value), 'init.sql')
        return str(init_sql_path)

    except Exception as e:
        click.echo(Icons.CROSS.value)
        click.echo(f'Error creating init sql: {e}', err=True)
        sys.exit(1)


def write_traefik_configs(ctx) -> Tuple[str, str]:
    click.echo('Setting up traefik... ', nl=False)
    try:
        traefik_config, router_config = templates.render_traefik_configs(
            http_port=ctx.obj['http']['port'],
            https_port=ctx.obj['https']['port'],
            https_enabled=True,
            domain=ctx.obj['https']['domain'],
            certs=ctx.obj['https']['certs']
        )

        traefik_path = os.path.join(
            ctx.obj['install_dir'],
            str(PHTDirectories.CONFIG_DIR.value),
            'traefik'
        )

        traefik_config_path = os.path.join(traefik_path, "traefik.yml")
        router_config_path = os.path.join(traefik_path, "config.yml")

        os.makedirs(traefik_path, exist_ok=True)

        with open(traefik_config_path, 'w') as f:
            f.write(traefik_config)

        with open(router_config_path, 'w') as f:
            f.write(router_config)

        click.echo(Icons.CHECKMARK.value)
        host_path = ctx.obj.get("host_path")
        if host_path:
            traefik_config_path = os.path.join(host_path, str(PHTDirectories.CONFIG_DIR.value), "traefik", "traefik.yml")
            router_config_path = os.path.join(host_path, str(PHTDirectories.CONFIG_DIR.value), "traefik", "config.yml")
        return str(traefik_config_path), str(router_config_path)

    except Exception as e:
        click.echo(Icons.CROSS.value)
        click.echo(f'Error: {e}', err=True)
        raise e


def write_airflow_config(ctx) -> str:
    click.echo('Setting up airflow... ', nl=False)
    try:

        db_connection_string = f"postgresql+psycopg2://{ctx.obj['db']['admin_user']}:{ctx.obj['db']['admin_password']}" \
                               f"@postgres/airflow"
        airflow_config = templates.render_airflow_config(
            sql_alchemy_conn=db_connection_string,
            domain=ctx.obj['https']['domain']
        )

        host_path = ctx.obj.get('host_path')
        if host_path:
            path = host_path
        else:
            path = ctx.obj['install_dir']

        airflow_config_path = os.path.join(
            path,
            str(PHTDirectories.CONFIG_DIR.value),
            'airflow.cfg'
        )

        write_path = os.path.join(
            ctx.obj['install_dir'],
            str(PHTDirectories.CONFIG_DIR.value),
            'airflow.cfg'
        )

        with open(write_path, 'w') as f:
            f.write(airflow_config)

        click.echo(Icons.CHECKMARK.value)
        return str(airflow_config_path)

    except Exception as e:
        click.echo(Icons.CROSS.value)
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


def write_compose_file(ctx):
    host_path = ctx.obj.get("host_path")
    if host_path:
        host_path = os.path.join(ctx.obj["host_path"], "docker-compose.yml")

    compose_path = os.path.join(ctx.obj["install_dir"], "docker-compose.yml")
    click.echo(f'Writing compose file to {host_path if host_path else compose_path}... ', nl=False)

    content = templates.render_compose(config=ctx.obj)
    with open(compose_path, 'w') as f:
        f.write(content)

    click.echo(Icons.CHECKMARK.value)
